import json
import os
from pathlib import Path

import pytest

from herdr_jj import adopt
from herdr_jj.lib.jj import JjError, JwError, Workspace


def write_event(monkeypatch, payload):
    monkeypatch.setenv(
        "HERDR_PLUGIN_EVENT_JSON", json.dumps(payload) if payload else ""
    )


def make_git_worktree(path: Path, primary: Path, name: str) -> None:
    path.mkdir(parents=True)
    primary.mkdir(parents=True)
    (primary / ".jj").mkdir()
    (path / ".git").write_text(f"gitdir: {primary}/.git/worktrees/{name}\n")


def test_event_payload_and_checkout_path_fallbacks(monkeypatch):
    write_event(
        monkeypatch,
        {"worktree": {"checkout_path": "/wt/a", "branch": "feature"}},
    )
    payload = adopt.event_payload(os.environ)
    assert adopt.checkout_path(payload) == Path("/wt/a")
    assert adopt.branch(payload) == "feature"

    write_event(
        monkeypatch,
        {"workspace": {"worktree": {"checkout_path": "/wt/b"}}},
    )
    payload = adopt.event_payload(os.environ)
    assert adopt.checkout_path(payload) == Path("/wt/b")
    assert adopt.branch(payload) is None

    write_event(monkeypatch, {})
    assert adopt.checkout_path(adopt.event_payload(os.environ)) is None


def test_git_worktree_parses_primary_and_name(tmp_path):
    path = tmp_path / "wt"
    primary = tmp_path / "repo"
    make_git_worktree(path, primary, "feature")

    assert adopt.git_worktree(path) == (primary, "feature")
    assert adopt.git_worktree(primary) is None

    (path / ".git").write_text("gitdir: elsewhere\n")
    assert adopt.git_worktree(path) is None


def test_base_revset_prefers_branch_then_trunk_ladder(monkeypatch, tmp_path):
    calls = []

    def fake_jj(*args, **kwargs):
        calls.append(args)
        if "missing" in args:
            raise JjError("no such revision")
        return ""

    monkeypatch.setattr(adopt, "jj", fake_jj)
    assert adopt.base_revset("feature", tmp_path) == "feature"
    assert adopt.base_revset(None, tmp_path) == "trunk()"

    def always_fail(*args, **kwargs):
        raise JjError("none")

    monkeypatch.setattr(adopt, "jj", always_fail)
    assert adopt.base_revset(None, tmp_path) == "root()"


def test_adopt_skips_non_git_and_non_jj(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert adopt.git_worktree(plain) is None

    path = tmp_path / "wt"
    primary = tmp_path / "gitonly"
    path.mkdir()
    primary.mkdir()
    (path / ".git").write_text(f"gitdir: {primary}/.git/worktrees/feature\n")
    assert (primary / ".jj").is_dir() is False
    assert adopt.adopt(path, None) is None


def test_adopt_runs_jj_jw_and_links(monkeypatch, tmp_path):
    path = tmp_path / "wt"
    primary = tmp_path / "repo"
    make_git_worktree(path, primary, "feature")
    calls = []

    monkeypatch.setattr(
        adopt, "workspaces", lambda cwd: [Workspace(name="main", root=primary)]
    )
    monkeypatch.setattr(adopt, "jj", lambda *a, **k: calls.append(("jj", a, k)) or "")
    monkeypatch.setattr(adopt, "jw", lambda *a, **k: calls.append(("jw", a, k)) or "")

    assert adopt.adopt(path, "feature") == "feature"
    assert (
        "jj",
        ("-R", str(primary), "git", "worktree", "adopt", "feature"),
        {},
    ) in calls
    assert (
        "jw",
        ("adopt", "feature", "--base", "feature", "--bookmark", "feature"),
        {"cwd": path},
    ) in calls
    assert ("jw", ("links", "apply"), {"cwd": path}) in calls


def test_adopt_is_idempotent(monkeypatch, tmp_path):
    path = tmp_path / "wt"
    primary = tmp_path / "repo"
    make_git_worktree(path, primary, "feature")
    calls = []

    monkeypatch.setattr(
        adopt, "workspaces", lambda cwd: [Workspace(name="feature", root=path)]
    )
    monkeypatch.setattr(adopt, "jj", lambda *a, **k: calls.append(("jj", a)) or "")
    monkeypatch.setattr(adopt, "jw", lambda *a, **k: "")

    assert adopt.adopt(path, None) == "feature"
    assert not [c for c in calls if "worktree" in c[1]]


def test_run_reports_jj_failure(monkeypatch, tmp_path, capsys):
    path = tmp_path / "wt"
    primary = tmp_path / "repo"
    make_git_worktree(path, primary, "feature")
    write_event(
        monkeypatch, {"worktree": {"checkout_path": str(path), "branch": "feature"}}
    )

    def fail(*args, **kwargs):
        raise JjError("locked")

    monkeypatch.setattr(adopt, "workspaces", lambda cwd: [])
    monkeypatch.setattr(adopt, "jj", fail)
    assert adopt.run(None) == 1
    assert "locked" in capsys.readouterr().err


def test_run_survives_jw_failure(monkeypatch, tmp_path):
    path = tmp_path / "wt"
    primary = tmp_path / "repo"
    make_git_worktree(path, primary, "feature")
    write_event(
        monkeypatch, {"worktree": {"checkout_path": str(path), "branch": "feature"}}
    )

    def jw_fail(*args, **kwargs):
        raise JwError("not managed")

    monkeypatch.setattr(adopt, "workspaces", lambda cwd: [])
    monkeypatch.setattr(adopt, "jj", lambda *a, **k: "")
    monkeypatch.setattr(adopt, "jw", jw_fail)
    assert adopt.run(None) == 1


def test_run_skips_missing_path_quietly(monkeypatch):
    write_event(monkeypatch, {"worktree": {"checkout_path": "/gone/wt"}})
    assert adopt.run(None) == 0


def test_run_records_ledger_on_success(monkeypatch, tmp_path):
    from herdr_jj import state

    path = tmp_path / "wt"
    primary = tmp_path / "repo"
    make_git_worktree(path, primary, "feature")
    write_event(
        monkeypatch, {"worktree": {"checkout_path": str(path), "branch": "feature"}}
    )
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(adopt, "workspaces", lambda cwd: [])
    monkeypatch.setattr(adopt, "jj", lambda *a, **k: "")
    monkeypatch.setattr(adopt, "jw", lambda *a, **k: "")

    assert adopt.run(None) == 0
    assert state.read_ledger() == {
        str(path.resolve()): {"name": "feature", "repo": str(primary)}
    }


def test_run_reports_missing_checkout_path(monkeypatch, capsys):
    write_event(monkeypatch, {"unexpected": True})
    assert adopt.run(None) == 0
    assert "no checkout path" in capsys.readouterr().err
