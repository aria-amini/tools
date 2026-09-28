"""Workspace identity is a checkout path; herdr IDs are session-local handles."""

import json
import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


class DashboardError(RuntimeError):
    pass


AHEAD = "\uf062"
BEHIND = "\uf063"
PR_ICON = "\uf423"


def command(*args: str, cwd: Path | None = None, timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DashboardError(str(error)) from error
    if result.returncode:
        raise DashboardError(
            result.stderr.strip() or f"{args[0]} exited {result.returncode}"
        )
    return result.stdout


def read_jj(root: Path, *args: str, snapshot: bool = False) -> str:
    flags = () if snapshot else ("--ignore-working-copy",)
    return command("jj", "--no-pager", "--color=never", *flags, *args, cwd=root)


def revisions(root: Path, revset: str) -> tuple[str, ...]:
    return tuple(
        read_jj(
            root, "log", "--no-graph", "-r", revset, "-T", 'commit_id ++ "\\n"'
        ).splitlines()
    )


def single(root: Path, revset: str) -> str:
    found = revisions(root, revset)
    if len(found) != 1:
        raise DashboardError(f"Expected one revision for {revset}; found {len(found)}")
    return found[0]


def quote(value: str) -> str:
    return json.dumps(value)


def checkout(path: Path) -> Path | None:
    path = path.resolve()
    return next((p for p in (path, *path.parents) if (p / ".jj").is_dir()), None)


def repository(root: Path) -> Path:
    pointer = root / ".jj" / "repo"
    if pointer.is_dir():
        return root
    return (pointer.parent / pointer.read_text().strip()).resolve().parent.parent


def read_bookmarks(root: Path) -> dict[str, dict[str, str]]:
    output = read_jj(
        root,
        "bookmark",
        "list",
        "--all-remotes",
        "-T",
        'self.name() ++ "\\t" ++ self.remote() ++ "\\t" ++ '
        'if(self.present(), if(self.conflict(), "conflict", '
        'self.normal_target().commit_id()), "deleted") ++ "\\n"',
    )
    bookmarks: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        name, remote, target = line.split("\t", 2)
        if target != "deleted":
            bookmarks.setdefault(name, {})[remote] = target
    return bookmarks


def resolve_trunk(
    root: Path,
    bookmarks: dict[str, dict[str, str]] | None = None,
) -> tuple[str, str]:
    """The writable local trunk bookmark and its commit."""
    # A remote trunk revset is not a writable local bookmark.
    config = read_jj(root, "config", "list", "herdr-jj.trunk-bookmark")
    if bookmarks is None:
        bookmarks = read_bookmarks(root)
    if config.strip():
        name = tomllib.loads(config)["herdr-jj"]["trunk-bookmark"]
        if not isinstance(name, str) or not name:
            raise DashboardError("herdr-jj.trunk-bookmark must be a nonempty string")
    else:
        names = [name for name in ("main", "master") if "" in bookmarks.get(name, {})]
        if len(names) != 1:
            raise DashboardError(
                "Set jj config herdr-jj.trunk-bookmark to one local bookmark"
            )
        name = names[0]
    target = bookmarks.get(name, {}).get("")
    if not target or target == "conflict":
        raise DashboardError(f"Expected one revision for bookmark {name}")
    return name, target


def trunk_bookmark(root: Path) -> str:
    return resolve_trunk(root)[0]


def parse_timestamp(stamp: str) -> int | None:
    match = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", stamp)
    if not match:
        return None
    return int(
        datetime.strptime(f"{match[1]} {match[2]}", "%Y-%m-%d %H:%M:%S")
        .astimezone()
        .timestamp()
    )


@dataclass(frozen=True)
class Agent:
    pane_id: str
    terminal_id: str
    session_id: str
    name: str
    status: str


@dataclass(frozen=True)
class Row:
    root: Path | None
    repo: Path | None
    name: str
    agents: tuple[Agent, ...] = ()
    sessions: tuple[str, ...] = ()
    ahead: int | None = None
    delta: str = "—"
    flags: str = ""
    error: str = ""
    is_primary: bool = False
    dirty: bool = False
    last_commit_ts: int | None = None
    bookmark: str = ""
    bookmark_state: str = "no bookmark"
    remote_state: str = ""
    current: bool = False
    pr: str = ""
    pr_state: str = ""

    @property
    def agent_summary(self) -> str:
        if len(self.agents) < 2:
            return self.status
        counts = {
            state: sum(a.status == state for a in self.agents)
            for state in ("blocked", "working", "done", "idle", "unknown")
        }
        return " · ".join(
            f"{count} {state}" for state, count in counts.items() if count
        )

    @property
    def key(self) -> str:
        return str(self.root) if self.root else f"{self.repo}::{self.name}"

    @property
    def display(self) -> str:
        if self.repo is None:
            return self.name
        if self.is_primary:
            return self.repo.name
        return f"{self.repo.name}/{self.name}"

    @property
    def active(self) -> bool:
        if "conflict" in self.flags or "missing" in self.flags:
            return "conflict" in self.flags
        return bool(self.sessions or self.agents or self.dirty or self.ahead)

    @property
    def status(self) -> str:
        states = {agent.status for agent in self.agents}
        return next(
            (
                s
                for s in ("blocked", "working", "done", "idle", "unknown")
                if s in states
            ),
            "shell" if self.sessions else "closed",
        )

    @property
    def tier(self) -> int:
        status = self.status
        if status in ("working", "blocked") or "conflict" in self.flags:
            return 0
        if self.agents:
            return 1
        if status == "shell":
            return 2
        if self.dirty:
            return 3
        if self.ahead:
            return 4
        return 5

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.tier, -(self.last_commit_ts or 0))


@dataclass
class Snapshot:
    rows: list[Row] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def bookmark_details(
    repo: Path,
    name: str,
    tip: str,
    bookmarks: dict[str, dict[str, str]] | None = None,
) -> tuple[str, str, str]:
    if bookmarks is None:
        bookmarks = read_bookmarks(repo)
    records = bookmarks.get(name, {})
    target = records.get("")
    if not target:
        return "", "no bookmark", ""
    if target == "conflict":
        return name, "conflict", ""
    beyond = len(revisions(repo, f"{target}..{tip}")) if target != tip else 0
    ahead = len(revisions(repo, f"{tip}..{target}")) if target != tip else 0
    if beyond and ahead:
        state = f"{AHEAD}{beyond} {BEHIND}{ahead}"
    elif beyond:
        state = f"{AHEAD}{beyond}"
    elif ahead:
        state = f"{BEHIND}{ahead}"
    else:
        state = "at tip"
    remotes = []
    for remote, revision in records.items():
        if remote and remote != "git":
            status = "synced" if revision == target else "differs"
            remotes.append(f"{remote} {status}")
    return name, state, ", ".join(remotes) or "local only"


def repo_prs(
    repo: Path, env: Mapping[str, str] | None = None, ttl: int = 120
) -> dict[str, tuple[str, str]]:
    """Head bookmark to PR number and state, cached briefly per repository.

    gh hits the network; without the cache every dashboard open and 5s
    refresh would pay its latency. Failures cache too, so a repo without
    GitHub does not retry on each refresh.
    """
    import hashlib
    import time

    from .state import state_dir

    env = os.environ if env is None else env
    path = (
        state_dir(env)
        / f"pr-cache-{hashlib.sha256(str(repo).encode()).hexdigest()[:12]}.json"
    )
    now = time.time()
    try:
        entry = json.loads(path.read_text())
        if now - entry["ts"] < ttl:
            return {name: tuple(info) for name, info in entry["prs"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        output = command(
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            "300",
            "--json",
            "number,state,headRefName",
            cwd=repo,
        )
        items = json.loads(output)
        prs = {
            item["headRefName"]: (f"#{item['number']}", item["state"]) for item in items
        }
    except (DashboardError, ValueError, KeyError, TypeError):
        prs = {}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": now, "prs": prs}))
    except OSError:
        pass
    return prs


def live_state() -> tuple[list[dict], list[dict]]:
    from .lib.herdr import herdr_bin

    def fetch(kind: str) -> list[dict]:
        try:
            data = json.loads(command(herdr_bin(), kind, "list"))
            if "error" in data:
                raise DashboardError(str(data["error"]))
            items = data["result"][f"{kind}s"]
            if not isinstance(items, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("workspace_id"), str)
                or (kind == "pane" and not isinstance(item.get("pane_id"), str))
                for item in items
            ):
                raise DashboardError(f"Invalid herdr {kind} list")
            return items
        except (ValueError, KeyError, TypeError) as error:
            raise DashboardError(f"Invalid herdr {kind} response") from error

    return fetch("workspace"), fetch("pane")


def live_paths(sessions: list[dict], panes: list[dict]) -> dict[str, Path]:
    paths = {}
    for session in sessions:
        path = (session.get("worktree") or {}).get("checkout_path")
        if path:
            paths[session["workspace_id"]] = Path(path).resolve()
    for pane in panes:
        cwd = pane.get("cwd")
        # herdr appends " (deleted)" to cwd strings whose directory is gone.
        if cwd and Path(cwd).is_dir():
            path = Path(cwd)
            paths.setdefault(pane["workspace_id"], checkout(path) or path.resolve())
    return paths


def collect(launch: Path | None = None) -> Snapshot:
    """Live herdr workspaces only; one row per jj checkout."""
    result = Snapshot()
    try:
        sessions, panes = live_state()
    except DashboardError as error:
        result.errors.append(str(error))
        sessions, panes = [], []
    paths = live_paths(sessions, panes)
    live = set(paths.values())
    launch_checkout = checkout(launch) if launch else None
    plain: set[Path] = set()
    repos: set[Path] = set()
    for path in live:
        root = checkout(path)
        if root is None:
            plain.add(path.resolve())
            continue
        try:
            repos.add(repository(root))
        except OSError as error:
            result.errors.append(f"{root}: {error}")

    def decorate(root: Path | None, repo: Path | None, name: str, **kwargs) -> Row:
        ids = tuple(
            sorted(
                {key for key, path in paths.items() if path == root}
                | {
                    p["workspace_id"]
                    for p in panes
                    if root is not None and checkout(Path(p.get("cwd", "/"))) == root
                }
            )
        )
        agents = tuple(
            Agent(
                p["pane_id"],
                p.get("terminal_id", ""),
                p["workspace_id"],
                p["agent"],
                p.get("agent_status", "unknown"),
            )
            for p in panes
            if root is not None
            and p.get("agent")
            and (
                checkout(Path(p.get("cwd", "/"))) == root
                or (p["workspace_id"] in ids and not checkout(Path(p.get("cwd", "/"))))
            )
        )
        return Row(
            root,
            repo,
            name,
            agents,
            ids,
            current=root is not None and root == launch_checkout,
            **kwargs,
        )

    def repo_rows(repo: Path) -> tuple[list[Row], list[str]]:
        rows = []
        try:
            output = read_jj(
                repo,
                "workspace",
                "list",
                "-T",
                'self.name() ++ "\\t" ++ self.root() ++ "\\t" ++ '
                'self.target().commit_id() ++ "\\t" ++ '
                'if(self.target().conflict(), "C", "-") ++ '
                'if(self.target().description(), "D", "-") ++ '
                'if(self.target().empty(), "E", "-") ++ "\\t" ++ '
                'self.target().committer().timestamp() ++ "\\t" ++ '
                'self.target().parents().map(|p| p.commit_id()).join(",") ++ "\\n"',
            )
            bookmarks = read_bookmarks(repo)
            try:
                trunk_name, trunk = resolve_trunk(repo, bookmarks)
                trunk_error = ""
            except DashboardError as error:
                trunk_name, trunk, trunk_error = "", "", str(error)
            prs = repo_prs(repo)
            for line in output.splitlines():
                name, path, head, head_flags, stamp, parents = line.split("\t", 5)
                root = Path(path).resolve() if path else None
                try:
                    described, empty = "D" in head_flags, "E" in head_flags
                    last_commit_ts = parse_timestamp(stamp)
                    flags = []
                    is_primary = root == repo
                    if root is None or not root.is_dir():
                        flags.append("missing")
                    ahead = None
                    delta = "—"
                    dirty = not empty
                    tip = head
                    if empty and not described:
                        tip = (
                            parents
                            if parents and "," not in parents
                            else single(repo, f"{head}-")
                        )
                    bookmark, bookmark_state, remote_state = (
                        bookmark_details(
                            repo, trunk_name if is_primary else name, tip, bookmarks
                        )
                        if not is_primary or trunk_name
                        else ("", "trunk unresolved", "")
                    )
                    # Trunk rows match the trunk bookmark; PRs there are noise.
                    pr, pr_state = (
                        prs.get(name, ("", ""))
                        if bookmark and not is_primary
                        else ("", "")
                    )
                    if trunk == tip:
                        ahead, delta = 0, "+0 −0"
                    elif trunk:
                        # One call yields the stack size and any conflict in it.
                        stack = read_jj(
                            repo,
                            "log",
                            "--no-graph",
                            "-r",
                            f"{trunk}..{tip}",
                            "-T",
                            'if(conflict,"C",".")',
                        )
                        ahead = len(stack.splitlines())
                        if "C" in stack:
                            flags.append("conflict")
                        base = single(repo, f"fork_point({trunk} | {tip})")
                        stat = read_jj(
                            repo, "diff", "--from", base, "--to", tip, "--stat"
                        )
                        match = re.search(
                            r"(\d+) insertions?\(\+\), (\d+) deletions?\(-\)", stat
                        )
                        if match:
                            delta = f"+{match[1]} −{match[2]}"
                    rows.append(
                        decorate(
                            root,
                            repo,
                            name,
                            ahead=ahead,
                            delta=delta,
                            flags=", ".join(flags),
                            is_primary=is_primary,
                            dirty=dirty,
                            last_commit_ts=last_commit_ts,
                            bookmark=bookmark,
                            bookmark_state=bookmark_state,
                            remote_state=remote_state,
                            pr=pr,
                            pr_state=pr_state,
                            error="" if is_primary else trunk_error,
                        )
                    )
                except DashboardError as error:
                    rows.append(decorate(root, repo, name, error=str(error)))
            # Only jj workspaces backed by a live herdr workspace appear.
            return [row for row in rows if row.root in live], []
        except (DashboardError, ValueError) as error:
            return [], [f"{repo}: {error}"]

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(repos)))) as pool:
        for rows, errors in pool.map(repo_rows, sorted(repos)):
            result.rows.extend(rows)
            result.errors.extend(errors)
    for root in sorted(plain):
        result.rows.append(decorate(root, None, root.name, flags="no jj workspace"))
    result.rows.sort(key=lambda row: (str(row.repo or row.root), row.name))
    return result
