import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from herdr_jj import state

from .conftest import jj, read_log, run_plugin, write_scenario

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(shutil.which("jj") is None, reason="jj not on PATH"),
]


def wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def context_env(env, cwd: Path):
    return {**env, "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(cwd)})}


def add_workspace(repo: Path, ws_root: Path, name: str) -> Path:
    dest = ws_root / repo.name / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    jj(repo, "workspace", "add", str(dest), "--name", name)
    return dest


class TestCreateFlow:
    def test_wizard_creates_and_opens_workspace(self, tmp_path, jj_repo, plugin_env):
        write_scenario(
            tmp_path,
            {
                "workspace list": {"workspaces": []},
                "workspace create": {
                    "workspace": {"workspace_id": "w-e2e"},
                    "root_pane": {"pane_id": "p-e2e"},
                },
                "pane split": {"pane": {"pane_id": "p2"}},
            },
        )
        env = context_env(plugin_env(), jj_repo)

        result = run_plugin(env, "wizard", stdin="feat\n")

        assert result.returncode == 0, result.stderr
        dest = tmp_path / "repo.feat"
        assert dest.is_dir()
        assert "feat" in jj(jj_repo, "workspace", "list")

        log = read_log(tmp_path)
        assert [
            "workspace",
            "create",
            "--cwd",
            str(dest),
            "--label",
            "feat",
            "--no-focus",
        ] in log
        assert ["pane", "split", "p-e2e", "--direction", "right", "--no-focus"] in log
        assert ["pane", "run", "p-e2e", "opencode"] in log
        focus_idx = log.index(["workspace", "focus", "w-e2e"])
        assert focus_idx > log.index(["pane", "run", "p-e2e", "opencode"])


class TestRemoveFlow:
    def test_remove_closes_forgets_and_deletes(self, tmp_path, jj_repo, plugin_env):
        ws_root = tmp_path / "workspaces"
        dest = add_workspace(jj_repo, ws_root, "feat")
        write_scenario(
            tmp_path,
            {
                "workspace list": {
                    "workspaces": [
                        {
                            "label": "feat",
                            "workspace_id": "w1",
                            "worktree": {"checkout_path": str(dest)},
                        }
                    ]
                }
            },
        )
        guard_dir = (
            tmp_path / "xdg" / "herdr" / "plugins" / "config" / "aamini.cd-guard"
        )
        guard_dir.mkdir(parents=True)
        (guard_dir / "roots").write_text(f"w1\t{dest}\n")

        result = run_plugin(plugin_env(), "remove", "--current", "--yes", cwd=dest)

        assert result.returncode == 0, result.stderr
        assert not dest.exists()
        assert "feat" not in jj(jj_repo, "workspace", "list")
        assert ["workspace", "close", "w1"] in read_log(tmp_path)
        assert "w1" not in (guard_dir / "roots").read_text()

    def test_remove_refuses_primary(self, tmp_path, jj_repo, plugin_env):
        write_scenario(tmp_path, {"workspace list": {"workspaces": []}})

        result = run_plugin(plugin_env(), "remove", "--current", "--yes", cwd=jj_repo)

        assert result.returncode == 1
        assert "default" in jj(jj_repo, "workspace", "list")
        assert jj_repo.is_dir()
        assert ["workspace", "close", "w1"] not in read_log(tmp_path)


class TestReapFlow:
    @staticmethod
    def removed_event(repo: Path, checkout: Path) -> str:
        payload = {
            "workspace_id": "w1",
            "forced": True,
            "workspace": {
                "worktree": {
                    "checkout_path": str(checkout),
                    "repo_root": str(repo),
                }
            },
            "worktree": {"path": str(checkout)},
        }
        return json.dumps({"data": payload})

    def test_reap_forgets_workspace_and_bookmark(self, tmp_path, jj_repo, plugin_env):
        checkout = add_workspace(jj_repo, tmp_path, "feat")
        jj(checkout, "bookmark", "set", "feat", "-r", "@")
        # herdr removed the checkout directory before the event fired.
        shutil.rmtree(checkout)
        env = plugin_env(
            JW_FAIL_REMOVE="1",
            HERDR_PLUGIN_EVENT_JSON=self.removed_event(jj_repo, checkout),
        )
        # adopt recorded the mapping while the directory existed
        state.write_ledger(
            {str(checkout.resolve()): {"name": "feat", "repo": str(jj_repo)}},
            env,
        )
        result = run_plugin(env, "reap", cwd=jj_repo)

        assert result.returncode == 0, result.stderr
        assert "feat" not in jj(jj_repo, "workspace", "list")
        assert "feat" not in jj(jj_repo, "bookmark", "list")

    def test_reap_uses_jw_when_it_succeeds(self, tmp_path, jj_repo, plugin_env):
        checkout = add_workspace(jj_repo, tmp_path, "feat")
        jj(checkout, "bookmark", "set", "feat", "-r", "@")
        shutil.rmtree(checkout)
        env = plugin_env(HERDR_PLUGIN_EVENT_JSON=self.removed_event(jj_repo, checkout))
        state.write_ledger(
            {str(checkout.resolve()): {"name": "feat", "repo": str(jj_repo)}},
            env,
        )
        result = run_plugin(env, "reap", cwd=jj_repo)

        assert result.returncode == 0, result.stderr
        assert "feat" not in jj(jj_repo, "workspace", "list")

    def test_reap_ignores_plain_git_worktree(self, tmp_path, plugin_env):
        git_repo = tmp_path / "gitrepo"
        subprocess.run(["git", "init", "-q", str(git_repo)], check=True)
        payload = {
            "workspace": {"worktree": {"repo_root": str(git_repo)}},
            "worktree": {"path": str(git_repo / ".wt")},
        }
        env = plugin_env(HERDR_PLUGIN_EVENT_JSON=json.dumps({"data": payload}))

        result = run_plugin(env, "reap", cwd=git_repo)

        assert result.returncode == 0, result.stderr


class TestPickerFlow:
    def test_picker_focuses_existing(self, tmp_path, jj_repo, plugin_env):
        ws_root = tmp_path / "workspaces"
        destination = add_workspace(jj_repo, ws_root, "feat")
        write_scenario(
            tmp_path,
            {
                "workspace list": {
                    "workspaces": [
                        {
                            "label": "feat",
                            "workspace_id": "w2",
                            "worktree": {"checkout_path": str(destination)},
                        }
                    ]
                }
            },
        )
        env = context_env(plugin_env(FZF_FAKE_NAME="feat"), jj_repo)

        result = run_plugin(env, "picker")

        assert result.returncode == 0, result.stderr
        log = read_log(tmp_path)
        assert ["workspace", "focus", "w2"] in log
        assert not any(call[:2] == ["workspace", "create"] for call in log)

    def test_picker_opens_new(self, tmp_path, jj_repo, plugin_env):
        ws_root = tmp_path / "workspaces"
        dest = add_workspace(jj_repo, ws_root, "feat")
        write_scenario(
            tmp_path,
            {
                "workspace list": {"workspaces": []},
                "workspace create": {
                    "workspace": {"workspace_id": "w-e2e"},
                    "root_pane": {"pane_id": "p-e2e"},
                },
                "pane split": {"pane": {"pane_id": "p2"}},
            },
        )
        env = context_env(plugin_env(FZF_FAKE_NAME="feat"), jj_repo)

        result = run_plugin(env, "picker")

        assert result.returncode == 0, result.stderr
        creates = [
            call for call in read_log(tmp_path) if call[:2] == ["workspace", "create"]
        ]
        assert creates
        assert str(dest) in creates[0]

    def test_picker_ctrl_d_removes_highlighted(self, tmp_path, jj_repo, plugin_env):
        ws_root = tmp_path / "workspaces"
        dest = add_workspace(jj_repo, ws_root, "feat")
        write_scenario(
            tmp_path,
            {
                "workspace list": {
                    "workspaces": [
                        {
                            "label": "feat",
                            "workspace_id": "w2",
                            "worktree": {"checkout_path": str(dest)},
                        }
                    ]
                }
            },
        )
        env = context_env(
            plugin_env(FZF_FAKE_KEY="ctrl-d", FZF_FAKE_NAME="feat"), jj_repo
        )

        result = run_plugin(env, "picker", stdin="y\n")

        assert result.returncode == 0, result.stderr
        assert not dest.exists()
        assert "feat" not in jj(jj_repo, "workspace", "list")
        assert ["workspace", "close", "w2"] in read_log(tmp_path)


class TestReporterEnsure:
    def test_ensure_is_idempotent_and_replaces_dead_pid(
        self, tmp_path, plugin_env, socket_server
    ):
        env = plugin_env()

        assert run_plugin(env, "reporter", "ensure").returncode == 0
        locks = list((tmp_path / "state").glob("reporter-*.lock"))
        assert len(locks) == 1
        first = json.loads(locks[0].read_text())
        assert first["socket_path"] == env["HERDR_SOCKET_PATH"]

        assert run_plugin(env, "reporter", "ensure").returncode == 0
        assert json.loads(locks[0].read_text())["pid"] == first["pid"]

        os.kill(first["pid"], signal.SIGTERM)
        assert wait_for(lambda: not _pid_alive(first["pid"]), timeout=10), (
            "reporter did not die"
        )

        assert run_plugin(env, "reporter", "ensure").returncode == 0
        replacement = json.loads(locks[0].read_text())
        assert replacement["pid"] != first["pid"]
        os.kill(replacement["pid"], signal.SIGTERM)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class TestReporterLoop:
    def test_reports_token_changes_and_exits_when_server_gone(
        self, tmp_path, jj_repo, plugin_env, socket_server
    ):
        write_scenario(
            tmp_path,
            {
                "workspace list": {"workspaces": [{"workspace_id": "w1"}]},
                "pane list": {
                    "panes": [
                        {
                            "workspace_id": "w1",
                            "cwd": str(jj_repo),
                            "tab_id": "w1:t1",
                            "pane_id": "w1:p1",
                        }
                    ]
                },
            },
        )
        socket_server.queue_event({"type": "workspace.focused", "workspace_id": "w1"})
        env = plugin_env()

        proc = subprocess.Popen(
            [sys.executable, "-m", "herdr_jj.main", "reporter", "run"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:

            def reports():
                return [
                    call
                    for call in read_log(tmp_path)
                    if call[:2] == ["workspace", "report-metadata"]
                ]

            assert wait_for(lambda: any("✓" in " ".join(c) for c in reports())), (
                "no clean-token report observed"
            )

            (jj_repo / "file.txt").write_text("dirty\n")
            assert wait_for(lambda: any("●" in " ".join(c) for c in reports())), (
                "no dirty-token report observed"
            )

            seqs = [int(c[c.index("--seq") + 1]) for c in reports()]
            assert seqs == sorted(seqs)
            assert all(c[c.index("--ttl-ms") + 1] == "90000" for c in reports())
        finally:
            socket_server.close()

        proc.wait(timeout=15)
        assert proc.returncode == 1
