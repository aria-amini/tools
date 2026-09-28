import json

import pytest

from herdr_jj import picker as picker_module
from herdr_jj.lib.jj import Workspace


@pytest.fixture
def layout(tmp_path):
    primary = tmp_path / "dotfiles"
    primary.mkdir()
    ws_root = tmp_path / "workspaces"
    feat = ws_root / "dotfiles" / "feat"
    feat.mkdir(parents=True)
    return primary, ws_root, feat


def run_picker(monkeypatch, layout, fzf_result, workspaces=None):
    primary, ws_root, feat = layout
    env = {
        "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"cwd": str(primary)}),
        "JJ_WORKSPACE_ROOT": str(ws_root),
    }
    calls = {"focus": [], "open": [], "remove": [], "wizard": [], "lines": None}

    monkeypatch.setattr(picker_module, "primary_root", lambda cwd: primary)
    monkeypatch.setattr(
        picker_module,
        "workspaces",
        lambda cwd: [Workspace("default", primary), Workspace("feat", feat)],
    )
    monkeypatch.setattr(
        picker_module, "status_token", lambda cwd, rev="@": f"tok-{rev}"
    )

    def fake_fzf(lines, preview=None, binds=None, expect=()):
        calls["lines"] = lines
        return fzf_result

    monkeypatch.setattr(picker_module, "fzf_select", fake_fzf)
    monkeypatch.setattr(picker_module, "list_workspaces", lambda: workspaces or [])
    monkeypatch.setattr(
        picker_module,
        "focus_workspace",
        lambda workspace_id: calls["focus"].append(workspace_id),
    )
    monkeypatch.setattr(
        picker_module,
        "open_workspace",
        lambda path, project_path=None, workspace_name=None: (
            calls["open"].append((path, project_path, workspace_name)) or 0
        ),
    )
    monkeypatch.setattr(
        picker_module,
        "remove_workspace",
        lambda *a, **k: calls["remove"].append(a) or 0,
    )
    monkeypatch.setattr(
        picker_module, "wizard", lambda *a, **k: calls["wizard"].append(a) or 0
    )

    rc = picker_module.picker(env)
    return rc, calls, primary, ws_root, feat


class TestPicker:
    def test_focuses_existing_herdr_workspace(self, monkeypatch, layout):
        rc, calls, _primary, _ws_root, _feat = run_picker(
            monkeypatch,
            layout,
            ("enter", "feat\ttok-feat@\tfeat"),
            workspaces=[
                {
                    "label": "feat",
                    "workspace_id": "w2",
                    "worktree": {"checkout_path": str(layout[2])},
                }
            ],
        )

        assert rc == 0
        assert calls["focus"] == ["w2"]
        assert calls["open"] == []

    def test_opens_new_when_no_herdr_match(self, monkeypatch, layout):
        rc, calls, primary, _ws_root, feat = run_picker(
            monkeypatch, layout, ("enter", "feat\ttok-feat@\t—"), workspaces=[]
        )

        assert rc == 0
        assert calls["open"] == [(feat, primary, "feat")]
        assert calls["focus"] == []

    def test_line_format_name_token_label(self, monkeypatch, layout):
        _rc, calls, *_ = run_picker(monkeypatch, layout, ("esc", None))

        assert calls["lines"] == [
            "default\ttok-default@\t—",
            "feat\ttok-feat@\t—",
        ]

    def test_lines_include_existing_labels(self, monkeypatch, layout):
        _rc, calls, *_ = run_picker(
            monkeypatch,
            layout,
            ("esc", None),
            workspaces=[
                {
                    "label": "feat",
                    "workspace_id": "w2",
                    "worktree": {"checkout_path": str(layout[2])},
                }
            ],
        )

        assert calls["lines"][1] == "feat\ttok-feat@\tfeat"

    def test_ctrl_d_invokes_remove_for_highlighted(self, monkeypatch, layout):
        rc, calls, primary, _ws_root, feat = run_picker(
            monkeypatch, layout, ("ctrl-d", "feat\ttok-feat@\t—")
        )

        assert rc == 0
        assert calls["remove"] == [(feat, primary)]

    def test_ctrl_n_invokes_wizard(self, monkeypatch, layout):
        rc, calls, *_ = run_picker(monkeypatch, layout, ("ctrl-n", None))

        assert rc == 0
        assert len(calls["wizard"]) == 1

    def test_cancel_exits_cleanly(self, monkeypatch, layout):
        rc, calls, *_ = run_picker(monkeypatch, layout, ("esc", None))

        assert rc == 0
        assert calls["open"] == []
        assert calls["remove"] == []
        assert calls["wizard"] == []

    def test_opens_primary_for_default_workspace(self, monkeypatch, layout):
        rc, calls, primary, _ws_root, _feat = run_picker(
            monkeypatch, layout, ("enter", "default\ttok-default@\t—"), workspaces=[]
        )

        assert rc == 0
        assert calls["open"] == [(primary, primary, "default")]

    def test_slash_hyphen_label_collision_uses_checkout_path(self, monkeypatch, layout):
        primary, _ws_root, feat = layout
        wrong = primary.parent / "other" / "feature-auth"
        workspaces = [
            {
                "label": "feature-auth",
                "workspace_id": "wrong",
                "worktree": {"checkout_path": str(wrong)},
            },
            {
                "label": "feature-auth",
                "workspace_id": "right",
                "worktree": {"checkout_path": str(feat)},
            },
        ]

        rc, calls, *_ = run_picker(
            monkeypatch,
            layout,
            ("enter", "feat\ttok-feat@\tfeature-auth"),
            workspaces=workspaces,
        )

        assert rc == 0
        assert calls["focus"] == ["right"]
        assert calls["open"] == []

    def test_same_label_in_other_repository_opens_selected_path(
        self, monkeypatch, layout
    ):
        other = layout[0].parent / "other-repo" / "feat"
        workspaces = [
            {
                "label": "feat",
                "workspace_id": "other",
                "worktree": {"checkout_path": str(other)},
            }
        ]

        rc, calls, primary, _ws_root, feat = run_picker(
            monkeypatch,
            layout,
            ("enter", "feat\ttok-feat@\t—"),
            workspaces=workspaces,
        )

        assert rc == 0
        assert calls["focus"] == []
        assert calls["open"] == [(feat, primary, "feat")]
