import json

from herdr_jj import actions as actions_module
from herdr_jj.lib.jj import JjError


def run_action(monkeypatch, tmp_path, action, cwd=None, jj_ok=True):
    calls = {"herdr": [], "ensure": []}
    cwd = cwd or tmp_path
    env = {
        "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(cwd)}),
        "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "state"),
    }

    if jj_ok:
        monkeypatch.setattr(actions_module, "primary_root", lambda c: tmp_path)
    else:

        def fail(c):
            raise JjError("jj root failed (1): not a repo")

        monkeypatch.setattr(actions_module, "primary_root", fail)

    monkeypatch.setattr(
        actions_module, "herdr", lambda *a: calls["herdr"].append(a) or {}
    )
    monkeypatch.setattr(
        actions_module, "ensure", lambda e: calls["ensure"].append(e) or 0
    )

    rc = action(env)
    return rc, env, calls


class TestPick:
    def test_opens_picker_pane(self, monkeypatch, tmp_path):
        rc, _env, calls = run_action(monkeypatch, tmp_path, actions_module.pick)

        assert rc == 0
        assert calls["herdr"] == [
            (
                "plugin",
                "pane",
                "open",
                "--plugin",
                "aamini.jj",
                "--entrypoint",
                "picker",
            )
        ]


class TestMenu:
    def test_opens_menu_pane_outside_jj_repo(self, monkeypatch, tmp_path):
        # menu must not require a jj repo: it picks the project first.
        def fail(c):
            raise JjError("jj root failed (1): not a repo")

        monkeypatch.setattr(actions_module, "primary_root", fail)
        rc, _env, calls = run_action(monkeypatch, tmp_path, actions_module.menu)
        assert rc == 0
        assert calls["herdr"] == [
            (
                "plugin",
                "pane",
                "open",
                "--plugin",
                "aamini.jj",
                "--entrypoint",
                "menu",
            )
        ]


class TestNew:
    def test_opens_new_pane_from_context_cwd(self, monkeypatch, tmp_path):
        rc, _env, calls = run_action(monkeypatch, tmp_path, actions_module.new)

        assert rc == 0
        assert calls["herdr"] == [
            (
                "plugin",
                "pane",
                "open",
                "--plugin",
                "aamini.jj",
                "--entrypoint",
                "new",
            )
        ]
        assert json.loads((tmp_path / "state" / "picker-context.json").read_text()) == {
            "cwd": str(tmp_path)
        }

    def test_works_outside_jj_repo(self, monkeypatch, tmp_path):
        def fail(c):
            raise JjError("jj root failed (1): not a repo")

        monkeypatch.setattr(actions_module, "primary_root", fail)
        rc, _env, calls = run_action(monkeypatch, tmp_path, actions_module.new)

        assert rc == 0
        assert calls["herdr"][0][:2] == ("plugin", "pane")
