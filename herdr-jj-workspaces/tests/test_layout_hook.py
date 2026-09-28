import json
from pathlib import Path
from unittest.mock import patch

from herdr_jj import layout_hook
from herdr_jj.lib import herdr as herdr_module
from herdr_jj.lib.layout import Layout, apply


def write_event(monkeypatch, payload):
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps(payload))


def make_event(checkout: Path, repo_root: Path) -> dict:
    return {
        "workspace": {
            "workspace_id": "w1",
            "worktree": {"checkout_path": str(checkout), "repo_root": str(repo_root)},
        },
        "worktree": {"checkout_path": str(checkout)},
    }


def test_run_splits_single_pane_workspace(monkeypatch, tmp_path):
    checkout = tmp_path / "wt"
    checkout.mkdir()
    (tmp_path / ".herdr.toml").write_text('[layout]\nagent = "oc"\nsetup = "htop"\n')
    write_event(monkeypatch, make_event(checkout, tmp_path))
    calls = []

    def fake_herdr(*args):
        calls.append(args)
        if args[:2] == ("pane", "list"):
            return {"panes": [{"pane_id": "w1:p1"}]}
        if args[:2] == ("pane", "split"):
            return {"pane": {"pane_id": "w1:p2"}}
        return {}

    with (
        patch.object(layout_hook, "herdr", side_effect=fake_herdr),
        patch.object(herdr_module, "herdr", side_effect=fake_herdr),
    ):
        assert layout_hook.run(None) == 0

    assert calls == [
        ("pane", "list", "--workspace", "w1"),
        ("pane", "split", "w1:p1", "--direction", "right", "--no-focus"),
        ("pane", "run", "w1:p1", "oc"),
        ("pane", "run", "w1:p2", "htop"),
    ]


def test_run_skips_multi_pane_workspace(monkeypatch, tmp_path):
    checkout = tmp_path / "wt"
    checkout.mkdir()
    write_event(monkeypatch, make_event(checkout, tmp_path))
    calls = []

    def fake_herdr(*args):
        calls.append(args)
        if args[:2] == ("pane", "list"):
            return {"panes": [{"pane_id": "w1:p1"}, {"pane_id": "w1:p2"}]}
        return {}

    with (
        patch.object(layout_hook, "herdr", side_effect=fake_herdr),
        patch.object(herdr_module, "herdr", side_effect=fake_herdr),
    ):
        assert layout_hook.run(None) == 0

    assert calls == [("pane", "list", "--workspace", "w1")]


def test_run_skips_missing_checkout_quietly(monkeypatch):
    write_event(monkeypatch, {"worktree": {"checkout_path": "/gone/wt"}})
    with (
        patch.object(layout_hook, "herdr") as herdr_mock,
        patch.object(herdr_module, "herdr") as lib_mock,
    ):
        assert layout_hook.run(None) == 0
    herdr_mock.assert_not_called()
    lib_mock.assert_not_called()


def test_run_resolves_workspace_by_checkout_path(monkeypatch, tmp_path):
    checkout = tmp_path / "wt"
    checkout.mkdir()
    write_event(
        monkeypatch,
        {"worktree": {"checkout_path": str(checkout)}},
    )
    calls = []

    def fake_herdr(*args):
        calls.append(args)
        if args[:2] == ("workspace", "list"):
            return {
                "workspaces": [
                    {"workspace_id": "w9", "worktree": {"checkout_path": str(checkout)}}
                ]
            }
        if args[:2] == ("pane", "list"):
            return {"panes": [{"pane_id": "w9:p1"}]}
        if args[:2] == ("pane", "split"):
            return {"pane": {"pane_id": "w9:p2"}}
        return {}

    with (
        patch.object(layout_hook, "herdr", side_effect=fake_herdr),
        patch.object(herdr_module, "herdr", side_effect=fake_herdr),
    ):
        assert layout_hook.run(None) == 0

    assert calls[0] == ("workspace", "list")
    assert calls[1] == ("pane", "list", "--workspace", "w9")


def test_run_survives_missing_workspace(monkeypatch, tmp_path, capsys):
    checkout = tmp_path / "wt"
    checkout.mkdir()
    write_event(monkeypatch, {"worktree": {"checkout_path": str(checkout)}})

    def fake_herdr(*args):
        if args[:2] == ("workspace", "list"):
            return {"workspaces": []}
        raise AssertionError("unexpected call")

    with patch.object(layout_hook, "herdr", side_effect=fake_herdr):
        assert layout_hook.run(None) == 0
    assert "no herdr workspace" in capsys.readouterr().err


def test_run_reports_herdr_failure(monkeypatch, tmp_path, capsys):
    checkout = tmp_path / "wt"
    checkout.mkdir()
    write_event(monkeypatch, make_event(checkout, tmp_path))

    def fail(*args, **kwargs):
        raise herdr_module.HerdrError("socket gone")

    with (
        patch.object(layout_hook, "herdr", side_effect=fail),
        patch.object(herdr_module, "herdr", side_effect=fail),
    ):
        assert layout_hook.run(None) == 1
    assert "socket gone" in capsys.readouterr().err


def test_repo_root_falls_back_to_checkout_for_non_jj(tmp_path):
    checkout = tmp_path / "wt"
    checkout.mkdir()
    assert layout_hook.repo_root({}, checkout) == checkout
    assert layout_hook.repo_root({"workspace": {}}, checkout) == checkout


def test_apply_runs_setup_only_when_configured(monkeypatch):
    calls = []

    def fake_herdr(*args):
        calls.append(args)
        if args[:2] == ("pane", "split"):
            return {"pane": {"pane_id": "p2"}}
        return {}

    with patch.object(herdr_module, "herdr", side_effect=fake_herdr):
        assert apply("p1", Layout(agent="oc", setup="")) == "p2"

    assert calls == [
        ("pane", "split", "p1", "--direction", "right", "--no-focus"),
        ("pane", "run", "p1", "oc"),
    ]
