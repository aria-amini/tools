"""Sidebar token reporter: per-server daemon reporting jj status via metadata.

One daemon per herdr server, armed by the workspace.created event and by
cold-start calls from other subcommands. A lockfile keyed by the sha1 of the
server socket path makes arming idempotent; stale locks (dead pid, different
socket) are replaced.
"""

import argparse
import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import state
from .lib.herdr import HerdrError, herdr
from .lib.jj import JjError
from .lib.sidebar import sidebar_tokens

Spawner = Callable[[list[str], Path], int]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
TokenFn = Callable[[dict], dict[str, str]]

SOURCE = "aamini.jj"
REPORT_TTL_MS = 90000
FOCUSED_INTERVAL = 5.0
BACKGROUND_INTERVAL = 30.0
ERROR_BUDGET_S = 30.0
CONNECT_ATTEMPTS = 3


def lock_path(env: Mapping[str, str], socket_path: str) -> Path:
    digest = hashlib.sha1(socket_path.encode()).hexdigest()
    return state.state_dir(env) / f"reporter-{digest}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _default_spawn(argv: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    return proc.pid


def ensure(
    env: Mapping[str, str] | None = None,
    spawn: Spawner = _default_spawn,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> int:
    env = os.environ if env is None else env
    socket_path = env.get("HERDR_SOCKET_PATH", "")
    path = lock_path(env, socket_path)

    try:
        lock = json.loads(path.read_text())
        if lock["socket_path"] == socket_path and pid_alive(int(lock["pid"])):
            return 0
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        pass

    log_path = state.state_dir(env) / "reporter.log"
    try:
        pid = spawn(["herdr-jj", "reporter", "run"], log_path)
    except OSError as error:
        print(f"herdr-jj reporter: spawn failed: {error}", file=sys.stderr)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "socket_path": socket_path}))
    return 0


@dataclass
class Report:
    workspace_id: str
    tokens: dict[str, str]
    cleared: tuple[str, ...] = ()


def workspace_cwds(herdr_fn: Callable[..., dict] = herdr) -> dict[str, str]:
    """One cwd per workspace, taken from its first pane.

    workspace list stopped returning cwd in herdr 0.8.2; pane list still
    carries it. The first pane by (tab, pane) id approximates the root pane.
    """
    panes = herdr_fn("pane", "list").get("panes", [])
    chosen: dict[str, tuple[tuple[str, str], str]] = {}
    for pane in panes:
        workspace_id = pane.get("workspace_id")
        cwd = pane.get("cwd")
        # herdr appends " (deleted)" to cwd strings whose directory is gone.
        if not workspace_id or not cwd or not Path(cwd).is_dir():
            continue
        key = (pane.get("tab_id") or "", pane.get("pane_id") or "")
        if workspace_id not in chosen or key < chosen[workspace_id][0]:
            chosen[workspace_id] = (key, cwd)
    return {workspace_id: cwd for workspace_id, (_, cwd) in chosen.items()}


def attach_cwds(workspaces: list[dict], cwds: dict[str, str]) -> None:
    for ws in workspaces:
        workspace_id = ws.get("workspace_id")
        cwd = cwds.get(workspace_id) if workspace_id else None
        if cwd:
            ws["cwd"] = cwd


def default_token_fn(ws: dict) -> dict[str, str]:
    return sidebar_tokens(Path(ws["cwd"]))


def compute_reports(
    workspaces: list[dict],
    cache: dict,
    token_fn: TokenFn = default_token_fn,
) -> list[Report]:
    """Build a report per workspace; the TTL acts as a lease, so every due
    poll republishes even unchanged tokens."""
    reports = []
    for ws in workspaces:
        cwd = ws.get("cwd")
        if not cwd:
            continue
        try:
            tokens = dict(token_fn(ws))
        except (JjError, OSError):
            continue
        previous = cache.get(ws["workspace_id"], {})
        cleared = tuple(sorted(set(previous) - set(tokens)))
        cache[ws["workspace_id"]] = tokens
        reports.append(Report(ws["workspace_id"], tokens, cleared))
    return reports


def publish(herdr_fn: Callable[..., dict], reports: list[Report]) -> None:
    for report in reports:
        args = [
            "workspace",
            "report-metadata",
            report.workspace_id,
            "--source",
            SOURCE,
        ]
        for name, value in report.tokens.items():
            args += ["--token", f"{name}={value}"]
        for name in report.cleared:
            args += ["--clear-token", name]
        # No --seq: a restarted daemon would resend seq 1, which the server
        # drops as stale. Synchronous CLI publishing needs no ordering guard.
        args += ["--ttl-ms", str(REPORT_TTL_MS)]
        herdr_fn(*args)


class SocketEvents:
    """Newline-delimited JSON events from the herdr server socket."""

    SUBSCRIPTIONS = ("workspace.focused", "workspace.created", "workspace.closed")

    def __init__(self, path: str):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(path)
        # The server drops silent connections; subscribe explicitly and the
        # first line read will be the acknowledgment.
        request = json.dumps(
            {
                "id": "herdr-jj-reporter",
                "method": "events.subscribe",
                "params": {
                    "subscriptions": [{"type": name} for name in self.SUBSCRIPTIONS]
                },
            }
        )
        self._sock.sendall(request.encode() + b"\n")
        self._sock.setblocking(False)
        self._buffer = b""

    def read(self, timeout: float) -> dict | None:
        ready, _, _ = select.select([self._sock], [], [], timeout)
        if not ready:
            return None
        chunk = self._sock.recv(65536)
        if not chunk:
            raise OSError("socket closed by server")
        self._buffer += chunk
        line, sep, self._buffer = self._buffer.partition(b"\n")
        if not sep:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None


def _default_connector(path: str) -> SocketEvents:
    return SocketEvents(path)


def run_loop(
    env: Mapping[str, str] | None = None,
    connector: Callable[[str], SocketEvents] = _default_connector,
    clock: Clock = time.monotonic,
    sleep: Sleeper = time.sleep,
    herdr_fn: Callable[..., dict] = herdr,
    token_fn: TokenFn = default_token_fn,
) -> int:
    env = os.environ if env is None else env
    socket_path = env.get("HERDR_SOCKET_PATH", "")

    events = None
    for _ in range(CONNECT_ATTEMPTS):
        try:
            events = connector(socket_path)
            break
        except OSError:
            sleep(1)
    if events is None:
        print("herdr-jj reporter: cannot connect to server socket", file=sys.stderr)
        return 1

    cache: dict = {}
    last_poll: dict[str, float] = {}
    focused_id: str | None = None
    error_since: float | None = None

    while True:
        try:
            workspaces = herdr_fn("workspace", "list").get("workspaces", [])
            error_since = None
        except HerdrError:
            now = clock()
            if error_since is None:
                error_since = now
            if now - error_since > ERROR_BUDGET_S:
                print("herdr-jj reporter: server unreachable, exiting", file=sys.stderr)
                return 1
            sleep(1)
            continue

        attach_cwds(workspaces, workspace_cwds(herdr_fn))

        now = clock()
        due = []
        for ws in workspaces:
            ws_id = ws["workspace_id"]
            interval = FOCUSED_INTERVAL if ws_id == focused_id else BACKGROUND_INTERVAL
            if now - last_poll.get(ws_id, -BACKGROUND_INTERVAL) >= interval:
                due.append(ws)
                last_poll[ws_id] = now
        publish(herdr_fn, compute_reports(due, cache, token_fn))

        try:
            event = events.read(FOCUSED_INTERVAL)
        except OSError:
            print("herdr-jj reporter: server socket lost, exiting", file=sys.stderr)
            return 1

        if event:
            event_type = event.get("type") or event.get("event") or ""
            ws_id = event.get("workspace_id") or (event.get("workspace") or {}).get(
                "workspace_id"
            )
            if ws_id and "focused" in event_type:
                focused_id = ws_id
            if ws_id and ("focused" in event_type or "created" in event_type):
                ws = next((w for w in workspaces if w["workspace_id"] == ws_id), None)
                if ws is not None:
                    publish(herdr_fn, compute_reports([ws], cache, token_fn))


def refresh_once(
    env: Mapping[str, str] | None = None,
    herdr_fn: Callable[..., dict] = herdr,
    token_fn: TokenFn = default_token_fn,
) -> int:
    env = os.environ if env is None else env
    workspaces = herdr_fn("workspace", "list").get("workspaces", [])
    attach_cwds(workspaces, workspace_cwds(herdr_fn))
    publish(herdr_fn, compute_reports(workspaces, {}, token_fn))
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("reporter", help="sidebar token reporter daemon")
    sub = parser.add_subparsers(dest="reporter_command", required=True)
    sub.add_parser("ensure", help="spawn the daemon unless one is armed").set_defaults(
        run=lambda args: ensure()
    )
    sub.add_parser("run", help="run the reporter loop in the foreground").set_defaults(
        run=lambda args: run_loop()
    )
    sub.add_parser(
        "refresh-once", help="report current tokens once and exit"
    ).set_defaults(run=lambda args: refresh_once())
