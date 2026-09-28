import json
from pathlib import Path

from herdr_jj import reap as reap_module
from herdr_jj import state
from herdr_jj.lib.jj import JjError, JwError, Workspace


def test_reap_resolves_name_from_path_convention(monkeypatch, tmp_path):
    primary = tmp_path / "repo"
    checkout = tmp_path / "repo.feat"
    calls = []
    monkeypatch.setattr(
        reap_module,
        "workspaces",
        lambda cwd: [Workspace("default", primary), Workspace("feat", checkout)],
    )
    monkeypatch.setattr(reap_module, "workspace_names", lambda cwd: ["default", "feat"])
    monkeypatch.setattr(
        reap_module,
        "jw_remove",
        lambda name, cwd: calls.append(("jw", name, cwd)),
    )

    assert (
        reap_module.reap_checkout(
            checkout, primary, reap_module.workspace_name_for(checkout, primary)
        )
        == 0
    )
    assert calls == [("jw", "feat", primary)]


def test_reap_resolves_name_from_ledger(monkeypatch, tmp_path):
    primary = tmp_path / "repo"
    checkout = tmp_path / "anywhere" / "custom-name"
    calls = []
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    state.write_ledger(
        {str(checkout.resolve()): {"name": "feat", "repo": str(primary)}}
    )
    monkeypatch.setattr(
        reap_module,
        "workspaces",
        lambda cwd: [Workspace("feat", checkout)],
    )
    monkeypatch.setattr(reap_module, "workspace_names", lambda cwd: ["feat"])
    monkeypatch.setattr(
        reap_module,
        "jw_remove",
        lambda name, cwd: calls.append(("jw", name, cwd)),
    )

    assert (
        reap_module.reap_checkout(
            checkout, primary, reap_module.workspace_name_for(checkout, primary)
        )
        == 0
    )
    assert calls == [("jw", "feat", primary)]
    assert state.read_ledger() == {}


def test_reap_falls_back_to_forget_and_bookmark(monkeypatch, tmp_path):
    primary = tmp_path / "repo"
    checkout = tmp_path / "repo.feat"
    calls = []

    def fail_jw(name, cwd):
        calls.append(("jw", name, cwd))
        raise JwError("jw remove feat failed (1): no such directory")

    monkeypatch.setattr(
        reap_module,
        "workspaces",
        lambda cwd: [Workspace("feat", checkout)],
    )
    monkeypatch.setattr(reap_module, "workspace_names", lambda cwd: ["feat"])
    monkeypatch.setattr(reap_module, "jw_remove", fail_jw)
    monkeypatch.setattr(
        reap_module,
        "forget_workspace",
        lambda name, cwd: calls.append(("forget", name, cwd)),
    )
    monkeypatch.setattr(
        reap_module,
        "delete_bookmark",
        lambda name, cwd: calls.append(("bookmark", name, cwd)),
    )

    rc = reap_module.reap_checkout(
        checkout, primary, "feat", "worktree/brave-harbor-e760"
    )

    assert rc == 0
    assert calls == [
        ("jw", "feat", primary),
        ("forget", "feat", primary),
        ("bookmark", "feat", primary),
        ("bookmark", "worktree/brave-harbor-e760", primary),
    ]


def test_reap_keeps_user_branch(monkeypatch, tmp_path):
    primary = tmp_path / "repo"
    checkout = tmp_path / "repo.feat"
    deleted = []
    monkeypatch.setattr(
        reap_module,
        "workspaces",
        lambda cwd: [Workspace("feat", checkout)],
    )
    monkeypatch.setattr(reap_module, "workspace_names", lambda cwd: ["feat"])
    monkeypatch.setattr(
        reap_module, "jw_remove", lambda name, cwd: deleted.append(("jw", name))
    )
    monkeypatch.setattr(
        reap_module, "delete_bookmark", lambda name, cwd: deleted.append(name)
    )

    assert reap_module.reap_checkout(checkout, primary, "feat", "main") == 0
    assert deleted == [("jw", "feat")]


def test_reap_quiet_when_already_forgotten(monkeypatch, tmp_path, capsys):
    primary = tmp_path / "repo"
    monkeypatch.setattr(
        reap_module, "workspaces", lambda cwd: [Workspace("default", primary)]
    )
    monkeypatch.setattr(reap_module, "workspace_names", lambda cwd: ["default"])
    monkeypatch.setattr(
        reap_module,
        "jw_remove",
        lambda name, cwd: (_ for _ in ()).throw(AssertionError("called")),
    )

    assert reap_module.reap_checkout(tmp_path / "repo.feat", primary, "feat") == 0
    assert capsys.readouterr().out == ""


def test_reap_refuses_primary(monkeypatch, tmp_path, capsys):
    primary = tmp_path / "repo"
    monkeypatch.setattr(
        reap_module, "workspaces", lambda cwd: [Workspace("default", primary)]
    )
    monkeypatch.setattr(reap_module, "workspace_names", lambda cwd: ["default"])

    assert reap_module.reap_checkout(primary, primary, "default") == 1
    assert "primary" in capsys.readouterr().err


def test_workspace_name_convention_requires_sibling(tmp_path):
    primary = tmp_path / "code" / "repo"
    assert reap_module.workspace_name_for(tmp_path / "repo.feat", primary) is None
    assert reap_module.workspace_name_for(tmp_path / "code" / "other", primary) is None
    assert (
        reap_module.workspace_name_for(tmp_path / "code" / "repo.feat", primary)
        == "feat"
    )


def test_run_reads_event_payload(monkeypatch, tmp_path):
    primary = tmp_path / "repo"
    (primary / ".jj").mkdir(parents=True)
    payload = {
        "workspace_id": "w1",
        "forced": True,
        "workspace": {
            "worktree": {
                "checkout_path": str(tmp_path / "repo.feat"),
                "repo_root": str(primary),
            }
        },
        "worktree": {"path": str(tmp_path / "repo.feat")},
    }
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps({"data": payload}))
    seen = []
    monkeypatch.setattr(
        reap_module,
        "reap_checkout",
        lambda path, primary, name, branch=None: (
            seen.append((path, primary, name)) or 0
        ),
    )

    assert reap_module.run(None) == 0
    assert seen == [(tmp_path / "repo.feat", primary, "feat")]


def test_run_skips_plain_git_repo(monkeypatch, tmp_path):
    payload = {"worktree": {"path": str(tmp_path / "repo.feat")}}
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps(payload))
    monkeypatch.setattr(
        reap_module,
        "reap_checkout",
        lambda path, primary, name: (_ for _ in ()).throw(AssertionError("called")),
    )

    assert reap_module.run(None) == 0


def test_run_skips_missing_repo_root(monkeypatch, tmp_path):
    payload = {
        "workspace": {"worktree": {"repo_root": str(tmp_path / "gone")}},
        "worktree": {"path": str(tmp_path / "gone.feat")},
    }
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps(payload))
    monkeypatch.setattr(
        reap_module,
        "reap_checkout",
        lambda path, primary, name: (_ for _ in ()).throw(AssertionError("called")),
    )

    assert reap_module.run(None) == 0


def test_run_reports_payload_without_path(monkeypatch, capsys):
    monkeypatch.setenv(
        "HERDR_PLUGIN_EVENT_JSON", json.dumps({"data": {"forced": True}})
    )

    assert reap_module.run(None) == 0
    assert "no checkout path" in capsys.readouterr().err


def test_reap_reports_workspace_list_failure(monkeypatch, tmp_path, capsys):
    def fail(cwd: Path):
        raise JjError("jj workspace list failed (1): corrupted")

    monkeypatch.setattr(reap_module, "workspaces", fail)
    monkeypatch.setattr(reap_module, "workspace_names", fail)

    assert (
        reap_module.reap_checkout(tmp_path / "repo.feat", tmp_path / "repo", "feat")
        == 1
    )
    assert "corrupted" in capsys.readouterr().err
