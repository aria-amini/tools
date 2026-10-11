import argparse
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from herdr_jj import pitchfork_cleanup


def test_cleanup_targets_only_the_removed_checkout(monkeypatch, tmp_path):
    checkout = tmp_path / "portfolio.animations"
    worker = checkout / "worker"
    other = tmp_path / "portfolio.dialog"
    calls = []
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PITCHFORK_STATE_DIR", str(tmp_path / "state"))
    state = tmp_path / "state/state.toml"
    state.parent.mkdir()
    state.write_text(
        f'[daemons."animations/dev"]\npid = 123\ndir = "{checkout}"\n'
        f'[daemons."animations/worker"]\ndir = "{worker}"\n'
        f'[daemons."dialog/dev"]\npid = 456\ndir = "{other}"\n'
        f'[daemons."global/tls"]\npid = 789\ndir = "{checkout}"\n'
    )
    before = state.read_text()
    config = tmp_path / "config/pitchfork/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'[namespaces.animations]\ndir = "{checkout}"\n'
        '[slugs.portfolio-animations]\nnamespace = "animations"\ndaemon = "dev"\n'
        f'[slugs.portfolio-dialog]\ndir = "{other}"\ndaemon = "dev"\n'
    )
    monkeypatch.setattr(
        pitchfork_cleanup.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    pitchfork_cleanup.cleanup_checkout(checkout)

    assert [command for command, _ in calls] == [
        ["pitchfork", "stop", "animations/dev"],
        ["pitchfork", "clean", "--prune", "--daemon", "animations/dev"],
        ["pitchfork", "clean", "--prune", "--daemon", "animations/worker"],
        ["pitchfork", "proxy", "remove", "portfolio-animations"],
    ]
    assert all(kwargs == {"cwd": Path.home(), "check": True} for _, kwargs in calls)
    assert state.read_text() == before


def test_cleanup_preserves_a_checkout_that_still_exists(monkeypatch, tmp_path):
    def unexpected_load(path):
        raise AssertionError("A live checkout must not trigger cleanup")

    monkeypatch.setattr(pitchfork_cleanup, "load", unexpected_load)
    pitchfork_cleanup.cleanup_checkout(tmp_path)


def test_removal_event_resolves_checkout_path(monkeypatch, tmp_path):
    checkout = tmp_path / "removed"
    calls = []
    monkeypatch.setenv(
        "HERDR_PLUGIN_EVENT_JSON",
        json.dumps({"data": {"worktree": {"checkout_path": str(checkout)}}}),
    )
    monkeypatch.setattr(
        pitchfork_cleanup.shutil, "which", lambda name: "/bin/pitchfork"
    )
    monkeypatch.setattr(pitchfork_cleanup, "cleanup_checkout", calls.append)
    assert pitchfork_cleanup.run(argparse.Namespace()) == 0
    assert calls == [checkout]


def test_plugin_registers_cleanup_on_removal():
    manifest = Path(__file__).parents[1] / "herdr-plugin/herdr-plugin.toml"
    config = tomllib.loads(manifest.read_text())
    assert {
        "on": "worktree.removed",
        "command": ["./bin/herdr-jj", "pitchfork-cleanup"],
    } in config["events"]


def test_failed_stop_does_not_skip_other_targets(monkeypatch, tmp_path):
    checkout = tmp_path / "removed"
    calls = []
    monkeypatch.setattr(
        pitchfork_cleanup,
        "load",
        lambda path: {
            "daemons": {
                "app/broken": {"dir": str(checkout), "pid": 123},
                "app/healthy": {"dir": str(checkout), "pid": 456},
            },
            "slugs": {"app": {"dir": str(checkout)}},
        },
    )

    def command(args, **kwargs):
        calls.append(args)
        if args == ["pitchfork", "stop", "app/broken"]:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(pitchfork_cleanup.subprocess, "run", command)
    with pytest.raises(pitchfork_cleanup.CleanupError, match="app/broken"):
        pitchfork_cleanup.cleanup_checkout(checkout)
    assert calls == [
        ["pitchfork", "stop", "app/broken"],
        ["pitchfork", "stop", "app/healthy"],
        ["pitchfork", "clean", "--prune", "--daemon", "app/healthy"],
        ["pitchfork", "proxy", "remove", "app"],
    ]


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("pitchfork") is None,
    reason="Pitchfork and a POSIX host are required",
)
def test_real_pitchfork_removes_daemon_and_slug(monkeypatch, tmp_path):
    home = tmp_path / "home"
    checkout = tmp_path / "app"
    home.mkdir()
    checkout.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "PITCHFORK_STATE_DIR": str(tmp_path / "pitchfork-state"),
        "PITCHFORK_PROXY_ENABLE": "false",
        "PITCHFORK_PROXY_AUTO_TRUST": "false",
    }
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = checkout / "pitchfork.toml"
    config.write_text('[daemons.dev]\nrun = "exec sleep 600"\nready_delay = 0\n')

    def command(*args, cwd=home):
        return subprocess.run(
            ["pitchfork", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

    try:
        command("proxy", "add", "app", "--daemon", "dev", "--dir", str(checkout))
        command("start", "dev", cwd=checkout)
        state = Path(env["PITCHFORK_STATE_DIR"]) / "state.toml"
        assert pitchfork_cleanup.load(state)["daemons"]["app/dev"]["pid"]
        config.unlink()
        checkout.rmdir()

        pitchfork_cleanup.cleanup_checkout(checkout)

        assert "app/dev" not in pitchfork_cleanup.load(state).get("daemons", {})
        global_config = Path(env["XDG_CONFIG_HOME"]) / "pitchfork/config.toml"
        assert "app" not in pitchfork_cleanup.load(global_config).get("slugs", {})
    finally:
        command("stop", "--all")
        command("supervisor", "stop")
