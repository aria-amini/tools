import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

FAKE_HERDR = """\
import json, os, sys
log = os.environ.get("HERDR_FAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
scenario_path = os.environ.get("HERDR_FAKE_SCENARIO")
scenario = json.loads(open(scenario_path).read()) if scenario_path else {}
key = " ".join(sys.argv[1:3])
print(json.dumps({"result": scenario.get(key, {})}))
"""

FAKE_FZF = """\
import os, sys
lines = sys.stdin.read().splitlines()
key = os.environ.get("FZF_FAKE_KEY", "")
want = os.environ.get("FZF_FAKE_NAME", "")
line = next((l for l in lines if l.startswith(want)), lines[0] if lines else "")
print(key)
if line:
    print(line)
"""

# jw's real path template is <repo>.<name> siblings; mirror that so e2e
# exercises the jw call path without a live jj-waltz install.
FAKE_JW = """\
import os, shutil, subprocess, sys

args = sys.argv[1:]
cmd = args[0] if args else ""
name = args[1] if len(args) > 1 else ""
cwd = os.getcwd()
root = cwd + "." + name


def jj(*a):
    subprocess.run(["jj", *a], check=True, capture_output=True)


if cmd == "add":
    jj("workspace", "add", root, "--name", name)
elif cmd == "remove":
    out = subprocess.run(
        ["jj", "workspace", "list", "-T", 'self.name() ++ "\\\\t" ++ self.root()'],
        capture_output=True, text=True, check=True,
    ).stdout
    root = next(
        line.split("\\t")[1]
        for line in out.splitlines()
        if line.split("\\t")[0] == name
    )
    jj("workspace", "forget", name)
    shutil.rmtree(root, ignore_errors=True)
"""


def _write_exe(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def fake_bins(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exe(bin_dir / "herdr", f"#!{sys.executable}\n{FAKE_HERDR}")
    _write_exe(bin_dir / "fzf", f"#!{sys.executable}\n{FAKE_FZF}")
    _write_exe(bin_dir / "jw", f"#!{sys.executable}\n{FAKE_JW}")
    _write_exe(
        bin_dir / "herdr-jj",
        f'#!/bin/sh\nexec {sys.executable} -m herdr_jj.main "$@"\n',
    )
    return bin_dir


@pytest.fixture
def jj_repo(tmp_path):
    if shutil.which("jj") is None:
        pytest.skip("jj not on PATH")
    repo = tmp_path / "repo"
    subprocess.run(["jj", "git", "init", str(repo)], check=True, capture_output=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.com")):
        subprocess.run(
            ["jj", "config", "set", "--repo", key, value],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["jj", "describe", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
    return repo


@pytest.fixture
def plugin_env(tmp_path, fake_bins):
    def make(**overrides):
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{fake_bins}{os.pathsep}{os.environ['PATH']}",
                "HERDR_FAKE_LOG": str(tmp_path / "herdr-calls.jsonl"),
                "HERDR_FAKE_SCENARIO": str(tmp_path / "scenario.json"),
                "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "state"),
                "HERDR_SOCKET_PATH": str(tmp_path / "herdr.sock"),
                "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
            }
        )
        env.update(overrides)
        return env

    return make


def write_scenario(tmp_path, scenario: dict) -> None:
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))


def read_log(tmp_path) -> list[list[str]]:
    path = tmp_path / "herdr-calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def run_plugin(env, *args, cwd=None, stdin=""):
    return subprocess.run(
        [sys.executable, "-m", "herdr_jj.main", *args],
        env=env,
        cwd=cwd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def jj(repo: Path, *args) -> str:
    result = subprocess.run(
        ["jj", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class FakeSocketServer:
    """Unix-socket stand-in for the herdr event stream."""

    def __init__(self, path: Path):
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(5)
        self._server.settimeout(0.2)
        self._conns: list[socket.socket] = []
        self._queued: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self._conns.append(conn)
            for data in self._queued:
                conn.sendall(data)

    def queue_event(self, event: dict) -> None:
        data = json.dumps(event).encode() + b"\n"
        self._queued.append(data)
        for conn in self._conns:
            try:
                conn.sendall(data)
            except OSError:
                pass

    def close(self):
        self._stop.set()
        for conn in self._conns:
            conn.close()
        self._server.close()
        self._thread.join(timeout=2)


@pytest.fixture
def socket_server(tmp_path):
    server = FakeSocketServer(tmp_path / "herdr.sock")
    yield server
    server.close()
