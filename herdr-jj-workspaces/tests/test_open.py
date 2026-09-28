import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_jj import open as open_module
from herdr_jj.lib import herdr as herdr_module
from herdr_jj.lib import layout
from herdr_jj.lib.herdr import HerdrError


def completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


class LabelTests(unittest.TestCase):
    def test_label_uses_workspace_dir(self):
        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        self.assertEqual(open_module.herdr_label(path), "plugin")


class SetupCommandTests(unittest.TestCase):
    def test_right_pane_runs_setup_command(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if args[:2] == ("workspace", "create"):
                return {
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "w9"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(open_module.open_workspace(path), 0)

        run_calls = [call for call in calls if call[:2] == ("pane", "run")]
        self.assertEqual(
            run_calls,
            [
                ("pane", "run", "p1", "opencode"),
                ("pane", "run", "p2", layout.DEFAULT_SETUP),
            ],
        )

    def test_layout_config_overrides_agent_and_setup(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if args[:2] == ("workspace", "create"):
                return {
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "w9"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        project = Path("/src/repo")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(
                open_module,
                "load_layout",
                return_value=layout.Layout("claude", "mise run bootstrap"),
            ),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(open_module.open_workspace(path, project), 0)

        run_calls = [call for call in calls if call[:2] == ("pane", "run")]
        self.assertEqual(
            run_calls,
            [
                ("pane", "run", "p1", "claude"),
                ("pane", "run", "p2", "mise run bootstrap"),
            ],
        )

    def test_empty_setup_config_skips_right_pane_command(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if args[:2] == ("workspace", "create"):
                return {
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "w9"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        project = Path("/src/repo")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(
                open_module, "load_layout", return_value=layout.Layout(setup="")
            ),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(open_module.open_workspace(path, project), 0)

        run_calls = [call for call in calls if call[:2] == ("pane", "run")]
        self.assertEqual(run_calls, [("pane", "run", "p1", "opencode")])


class FindWorkspaceTests(unittest.TestCase):
    def test_matches_by_label(self):
        payload = {
            "workspaces": [
                {"label": "ui", "workspace_id": "w1"},
                {"label": "plugin", "workspace_id": "w2"},
            ]
        }
        with patch.object(herdr_module, "herdr", return_value=payload):
            found = herdr_module.find_workspace("plugin")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["workspace_id"], "w2")

    def test_returns_none_without_match(self):
        with patch.object(herdr_module, "herdr", return_value={"workspaces": []}):
            self.assertIsNone(herdr_module.find_workspace("nope"))

    def test_find_by_worktree_path_matches_checkout_path(self):
        path = Path("/workspaces/repo/feature-auth")
        by_checkout = {
            "workspace_id": "checkout",
            "worktree": {"checkout_path": str(path)},
        }
        other = {
            "workspace_id": "other",
            "worktree": {"checkout_path": "/elsewhere"},
        }

        self.assertEqual(
            herdr_module.find_by_worktree_path(path, [other, by_checkout]),
            by_checkout,
        )
        self.assertIsNone(herdr_module.find_by_worktree_path(path, [other]))


class OpenWorkspaceTests(unittest.TestCase):
    def test_uses_explicit_workspace_name_for_custom_path(self):
        path = Path("/workspaces/feat/checkout")
        with (
            patch.object(
                open_module,
                "ensure_open",
                return_value=({"workspace": {"workspace_id": "w2"}}, False, []),
            ) as ensure_open,
            patch.object(open_module, "focus_workspace"),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(
                open_module.open_workspace(
                    path, Path("/src/dotfiles"), workspace_name="feat"
                ),
                0,
            )
        ensure_open.assert_called_once_with(
            "feat", path, project_path=Path("/src/dotfiles")
        )

    def test_focuses_existing_without_layout(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {
                            "label": "plugin",
                            "workspace_id": "w2",
                            "worktree": {
                                "checkout_path": "/home/u/.herdr/workspaces/dotfiles/plugin"
                            },
                        }
                    ]
                }
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm") as arm,
        ):
            self.assertEqual(open_module.open_workspace(path), 0)

        self.assertEqual(calls, [("workspace", "list"), ("workspace", "focus", "w2")])
        arm.assert_called_once_with("w2", path, {"w2"})

    def test_existing_workspace_focused_without_rerunning_hooks(self):
        calls = []
        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {
                            "label": "plugin",
                            "workspace_id": "w2",
                            "worktree": {"checkout_path": str(path)},
                        }
                    ]
                }
            return {}

        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(open_module.open_workspace(path), 0)

        self.assertEqual([call for call in calls if call[:2] == ("pane", "run")], [])
        self.assertIn(("workspace", "focus", "w2"), calls)

    def test_label_collision_does_not_focus_wrong_path(self):
        path = Path("/workspaces/repo/feature-auth")
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {
                            "label": "feature-auth",
                            "workspace_id": "wrong",
                            "worktree": {"checkout_path": "/other/repo/feature-auth"},
                        }
                    ]
                }
            if args[:2] == ("workspace", "create"):
                return {
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "right"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(
                open_module.open_workspace(path, workspace_name="feature/auth"), 0
            )

        self.assertNotIn(("workspace", "focus", "wrong"), calls)
        self.assertIn(("workspace", "focus", "right"), calls)

    def test_reopens_collision_suffixed_workspace_by_checkout_path(self):
        path = Path("/workspaces/repo/feature-auth-abc123")
        existing = {
            "label": "feature-auth-abc123",
            "workspace_id": "existing",
            "worktree": {"checkout_path": str(path)},
        }
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": [existing]}
            return {}

        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(
                open_module.open_workspace(path, workspace_name="feature/auth"), 0
            )

        self.assertIn(("workspace", "focus", "existing"), calls)
        self.assertNotIn(("workspace", "create"), [call[:2] for call in calls])

    def test_path_match_wins_before_base_label_collision(self):
        path = Path("/repos/one/feature-auth-abc123")
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {
                            "label": "feature-auth",
                            "workspace_id": "wrong",
                            "worktree": {"checkout_path": "/repos/two/feature-auth"},
                        },
                        {
                            "label": "feature-auth-abc123",
                            "workspace_id": "right",
                            "worktree": {"checkout_path": str(path)},
                        },
                    ]
                }
            return {}

        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(
                open_module.open_workspace(path, workspace_name="feature/auth"), 0
            )

        self.assertIn(("workspace", "focus", "right"), calls)
        self.assertNotIn(("workspace", "focus", "wrong"), calls)

    def test_creates_layout_and_runs_agent(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if args[:2] == ("workspace", "create"):
                return {
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "w9"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm") as arm,
        ):
            self.assertEqual(open_module.open_workspace(path), 0)

        self.assertEqual(
            calls,
            [
                ("workspace", "list"),
                (
                    "workspace",
                    "create",
                    "--cwd",
                    str(path),
                    "--label",
                    "plugin",
                    "--no-focus",
                ),
                ("pane", "split", "p1", "--direction", "right", "--no-focus"),
                ("pane", "run", "p1", "opencode"),
                ("pane", "run", "p2", layout.DEFAULT_SETUP),
                ("workspace", "focus", "w9"),
            ],
        )
        arm.assert_called_once_with("w9", path, {"w9"})

    def test_layout_setup_lands_on_split_pane(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if args[:2] == ("workspace", "create"):
                return {
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "w9"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm") as arm,
        ):
            self.assertEqual(open_module.open_workspace(path), 0)

        self.assertIn(("pane", "run", "p2", layout.DEFAULT_SETUP), calls)
        self.assertNotIn(("pane", "run", "p1", layout.DEFAULT_SETUP), calls)
        arm.assert_called_once_with("w9", path, {"w9"})

    def test_opens_via_worktree_open_when_project_path_given(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if args[:2] == ("worktree", "open"):
                return {
                    "already_open": False,
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "w9"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        primary = Path("/home/u/dotfiles")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(open_module.open_workspace(path, primary), 0)

        self.assertEqual(
            calls[:2],
            [
                ("workspace", "list"),
                (
                    "worktree",
                    "open",
                    "--cwd",
                    str(primary),
                    "--path",
                    str(path),
                    "--label",
                    "plugin",
                    "--no-focus",
                ),
            ],
        )
        self.assertNotIn(("workspace", "create"), [call[:2] for call in calls])
        self.assertEqual(calls[-1], ("workspace", "focus", "w9"))

    def test_falls_back_to_create_when_worktree_open_fails(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if args[:2] == ("worktree", "open"):
                raise herdr_module.HerdrError("not a worktree")
            if args[:2] == ("workspace", "create"):
                return {
                    "root_pane": {"pane_id": "p1"},
                    "workspace": {"workspace_id": "w9"},
                }
            if args[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "p2"}}
            return {}

        path = Path("/home/u/.herdr/workspaces/dotfiles/plugin")
        primary = Path("/home/u/dotfiles")
        with (
            patch.object(open_module, "herdr", side_effect=fake_herdr),
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(open_module.guard, "arm"),
        ):
            self.assertEqual(open_module.open_workspace(path, primary), 0)

        kinds = [call[:2] for call in calls]
        self.assertIn(("worktree", "open"), kinds)
        self.assertIn(("workspace", "create"), kinds)


class LayoutTests(unittest.TestCase):
    def test_defaults_without_config_file(self):
        loaded = layout.load(Path("/nonexistent/repo"))
        self.assertEqual(loaded, layout.Layout())

    def test_config_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".herdr.toml").write_text(
                '[layout]\nagent = "claude"\nsetup = "mise run bootstrap"\n'
            )
            self.assertEqual(
                layout.load(root),
                layout.Layout(agent="claude", setup="mise run bootstrap"),
            )

    def test_empty_setup_disables_setup_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".herdr.toml").write_text('[layout]\nsetup = ""\n')
            self.assertEqual(layout.load(root), layout.Layout(setup=""))

    def test_invalid_toml_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".herdr.toml").write_text("[layout\nbroken")
            self.assertEqual(layout.load(root), layout.Layout())

    def test_non_string_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".herdr.toml").write_text("[layout]\nagent = 3\nsetup = true\n")
            self.assertEqual(layout.load(root), layout.Layout())


class HerdrWrapperTests(unittest.TestCase):
    def test_raises_on_nonzero_exit(self):
        from herdr_jj.lib import herdr as herdr_module

        with (
            patch.object(subprocess, "run", return_value=completed("", 2)),
            self.assertRaises(HerdrError),
        ):
            herdr_module.herdr("workspace", "list")


if __name__ == "__main__":
    unittest.main()
