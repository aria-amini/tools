import json
from pathlib import Path

from herdr_jj import state
from herdr_jj import wizard as wizard_module
from herdr_jj.lib.jj import JjError, Workspace


def run_wizard(monkeypatch, tmp_path, env, name="feat"):
    repo = tmp_path / "dotfiles"
    calls = {}

    monkeypatch.setattr(wizard_module, "primary_root", lambda cwd: repo)

    def fake_create(ws_name, cwd, create_env=None):
        dest = Path((create_env or {})["JJ_WORKSPACE_ROOT"]) / repo.name / ws_name
        calls["create"] = (ws_name, cwd, create_env)
        return Workspace(ws_name, dest)

    def fake_open(path, project_path=None, workspace_name=None):
        calls["open"] = (path, project_path, workspace_name)
        return 0

    monkeypatch.setattr(wizard_module, "create_workspace", fake_create)
    monkeypatch.setattr(wizard_module, "open_workspace", fake_open)
    monkeypatch.setattr(
        wizard_module, "ensure", lambda env: calls.setdefault("ensure", env) or 0
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": name)

    rc = wizard_module.wizard(env, input_fn=input)
    return rc, repo, calls


class TestWizard:
    def test_creates_workspace_and_opens_it(self, monkeypatch, tmp_path):
        env = {
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(tmp_path)}),
            "JJ_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        }
        rc, repo, calls = run_wizard(monkeypatch, tmp_path, env)

        dest = tmp_path / "workspaces" / "dotfiles" / "feat"
        assert rc == 0
        assert calls["create"] == ("feat", tmp_path, env)
        assert calls["open"] == (dest, repo, "feat")

    def test_herdr_label_matches_herdr_ws_convention(self, monkeypatch, tmp_path):
        env = {
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(tmp_path)}),
            "JJ_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        }
        rc, _repo, calls = run_wizard(monkeypatch, tmp_path, env)

        from herdr_jj.open import herdr_label

        assert rc == 0
        assert herdr_label(calls["open"][0]) == "feat"

    def test_arms_reporter_ensure(self, monkeypatch, tmp_path):
        env = {
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(tmp_path)}),
            "JJ_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        }
        rc, _repo, calls = run_wizard(monkeypatch, tmp_path, env)

        assert rc == 0
        assert calls["ensure"] == env

    def test_reads_real_herdr_context_shape(self, monkeypatch, tmp_path):
        seen = {}

        def fake_primary(cwd):
            seen["cwd"] = cwd
            return tmp_path

        monkeypatch.setattr(wizard_module, "primary_root", fake_primary)
        monkeypatch.setattr(
            wizard_module,
            "create_workspace",
            lambda cwd, name, revision=None, env=None, **_: Workspace(
                name, tmp_path / name
            ),
        )
        monkeypatch.setattr(wizard_module, "open_workspace", lambda *a, **k: 0)
        monkeypatch.setattr(wizard_module, "ensure", lambda env: 0)

        env = {
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
                {
                    "focused_pane_cwd": str(tmp_path / "pane-dir"),
                    "workspace_cwd": str(tmp_path),
                    "workspace_id": "wAK",
                }
            ),
            "JJ_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        }
        rc = wizard_module.wizard(env, input_fn=lambda prompt="": "feat")

        assert rc == 0
        assert seen["cwd"] == Path(tmp_path / "pane-dir")

    def test_falls_back_to_workspace_cwd(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(
            wizard_module,
            "primary_root",
            lambda cwd: seen.setdefault("cwd", cwd) or tmp_path,
        )
        monkeypatch.setattr(
            wizard_module,
            "create_workspace",
            lambda cwd, name, revision=None, env=None, **_: Workspace(
                name, tmp_path / name
            ),
        )
        monkeypatch.setattr(wizard_module, "open_workspace", lambda *a, **k: 0)
        monkeypatch.setattr(wizard_module, "ensure", lambda env: 0)

        env = {
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
                {"workspace_cwd": str(tmp_path), "workspace_id": "wAK"}
            ),
            "JJ_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        }
        rc = wizard_module.wizard(env, input_fn=lambda prompt="": "feat")

        assert rc == 0
        assert seen["cwd"] == Path(tmp_path)

    def test_falls_back_to_state_dir_context(self, monkeypatch, tmp_path):
        env = {
            "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "state"),
            "JJ_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        }
        state.write_context(env, {"cwd": str(tmp_path)})
        rc, _repo, calls = run_wizard(monkeypatch, tmp_path, env)

        assert rc == 0
        assert "create" in calls

    def test_aborts_when_not_in_jj_repo(self, monkeypatch, tmp_path, capsys):
        def fail(cwd):
            raise JjError("jj root failed (1): not a repo")

        monkeypatch.setattr(wizard_module, "primary_root", fail)
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(tmp_path)})}

        opened = []
        monkeypatch.setattr(
            wizard_module, "open_workspace", lambda *a, **k: opened.append(a) or 0
        )

        rc = wizard_module.wizard(env, input_fn=lambda prompt="": "feat")
        assert rc == 1
        assert opened == []
        assert "not a repo" in capsys.readouterr().err

    def test_aborts_on_empty_name(self, monkeypatch, tmp_path, capsys):
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(tmp_path)})}
        rc, _repo, calls = run_wizard(monkeypatch, tmp_path, env, name="  ")

        assert rc == 1
        assert calls == {}
        assert "empty" in capsys.readouterr().err

    def test_reports_jj_add_failure_and_skips_open(self, monkeypatch, tmp_path, capsys):
        def fail_add(cwd, name, revision=None, env=None, **_):
            raise JjError("jj workspace add failed (1): name exists")

        monkeypatch.setattr(wizard_module, "primary_root", lambda cwd: tmp_path)
        monkeypatch.setattr(wizard_module, "create_workspace", fail_add)
        opened = []
        monkeypatch.setattr(
            wizard_module, "open_workspace", lambda *a, **k: opened.append(a) or 0
        )

        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(tmp_path)})}
        rc = wizard_module.wizard(env, input_fn=lambda prompt="": "feat")

        assert rc == 1
        assert opened == []
        assert "name exists" in capsys.readouterr().err
