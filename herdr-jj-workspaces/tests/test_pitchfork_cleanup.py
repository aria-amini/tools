import argparse
import json
import tomllib
from pathlib import Path

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
