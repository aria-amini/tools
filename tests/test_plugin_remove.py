from pathlib import Path

from herdr_jj import remove as remove_module
from herdr_jj.lib.jj import JjError, JwError, Workspace


def test_remove_uses_jw_then_closes(monkeypatch, tmp_path):
    primary = tmp_path / "dotfiles"
    target = tmp_path / "workspaces" / "dotfiles" / "feat"
    target.mkdir(parents=True)
    calls = []

    monkeypatch.setattr(remove_module, "repo_root", lambda cwd: target)
    monkeypatch.setattr(remove_module, "primary_root", lambda cwd: primary)
    monkeypatch.setattr(
        remove_module,
        "current_workspace",
        lambda cwd: Workspace("feat", target),
    )
    monkeypatch.setattr(
        remove_module,
        "jw_remove",
        lambda name, cwd: calls.append(("jw", name, cwd)),
    )
    monkeypatch.setattr(
        remove_module,
        "close_workspace",
        lambda name, path=None, primary=None: calls.append(("close", name, path)) or 0,
    )

    rc = remove_module.remove_current({}, assume_yes=True)

    assert rc == 0
    assert calls[0] == ("jw", "feat", primary)
    assert calls[1] == ("close", "feat", target)
    assert target.exists()  # jw owns the directory delete


def test_remove_falls_back_when_jw_fails(monkeypatch, tmp_path):
    primary = tmp_path / "dotfiles"
    target = tmp_path / "workspaces" / "dotfiles" / "feat"
    target.mkdir(parents=True)
    calls = []

    monkeypatch.setattr(remove_module, "repo_root", lambda cwd: target)
    monkeypatch.setattr(remove_module, "primary_root", lambda cwd: primary)
    monkeypatch.setattr(
        remove_module,
        "current_workspace",
        lambda cwd: Workspace("feat", target),
    )

    def fail_jw(name, cwd):
        calls.append(("jw", name, cwd))
        raise JwError("jw remove feat failed (1): not managed")

    monkeypatch.setattr(remove_module, "jw_remove", fail_jw)
    monkeypatch.setattr(
        remove_module,
        "forget_workspace",
        lambda name, cwd: calls.append(("forget", name, cwd)),
    )
    monkeypatch.setattr(
        remove_module,
        "close_workspace",
        lambda name, path=None, primary=None: calls.append(("close", name, path)) or 0,
    )

    rc = remove_module.remove_current({}, assume_yes=True)

    assert rc == 0
    assert calls == [
        ("jw", "feat", primary),
        ("forget", "feat", primary),
        ("close", "feat", target),
    ]
    assert not target.exists()


def test_remove_refuses_primary_workspace(monkeypatch, tmp_path, capsys):
    primary = tmp_path / "repo"
    monkeypatch.setattr(remove_module, "repo_root", lambda cwd: primary)
    monkeypatch.setattr(remove_module, "primary_root", lambda cwd: primary)
    monkeypatch.setattr(
        remove_module,
        "current_workspace",
        lambda cwd: Workspace("default", primary),
    )

    assert remove_module.remove_current({}, assume_yes=True) == 1
    assert "primary" in capsys.readouterr().err


def test_remove_does_not_close_when_aborted(monkeypatch, tmp_path, capsys):
    primary = tmp_path / "repo"
    target = tmp_path / "feat"
    monkeypatch.setattr(remove_module, "repo_root", lambda cwd: target)
    monkeypatch.setattr(remove_module, "primary_root", lambda cwd: primary)
    monkeypatch.setattr(
        remove_module,
        "current_workspace",
        lambda cwd: Workspace("feat", target),
    )
    closed = []
    monkeypatch.setattr(
        remove_module,
        "close_workspace",
        lambda *args: closed.append(args) or 0,
    )

    assert remove_module.remove_current({}, input_fn=lambda prompt: "n") == 1
    assert closed == []


def test_remove_fails_when_cwd_not_in_jj_repo(monkeypatch, tmp_path, capsys):
    def fail(cwd: Path):
        raise JjError("jj root failed (1): not a repo")

    monkeypatch.setattr(remove_module, "repo_root", fail)

    assert remove_module.remove_current({}, assume_yes=True) == 1
    assert "not a repo" in capsys.readouterr().err
