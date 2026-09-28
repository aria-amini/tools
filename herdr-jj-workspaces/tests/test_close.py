import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_jj import close as close_module
from herdr_jj.lib import herdr as herdr_module


class LabelTests(unittest.TestCase):
    def test_uses_workspace_name(self):
        self.assertEqual(herdr_module.workspace_label("plugin"), "plugin")

    def test_sanitizes_slashes(self):
        self.assertEqual(herdr_module.workspace_label("feat/login"), "feat-login")
        self.assertEqual(herdr_module.workspace_label("feat\\login"), "feat-login")


class CloseWorkspaceTests(unittest.TestCase):
    def test_closes_matching_workspace(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {"label": "ui", "workspace_id": "w1"},
                        {"label": "plugin", "workspace_id": "w2"},
                    ]
                }
            return {}

        with (
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(close_module.guard, "disarm") as disarm,
            patch.object(close_module.guard, "prune") as prune,
        ):
            self.assertEqual(close_module.close_workspace("plugin"), 0)

        self.assertEqual(calls, [("workspace", "list"), ("workspace", "close", "w2")])
        disarm.assert_called_once_with("w2")
        prune.assert_called_once_with({"w1"})

    def test_closes_by_worktree_path_when_label_differs(self):
        calls = []
        path = Path("/home/u/.herdr/workspaces/dotfiles/feat")

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {
                            "label": "other",
                            "workspace_id": "w3",
                            "worktree": {"checkout_path": str(path)},
                        },
                    ]
                }
            return {}

        with (
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(close_module.guard, "disarm") as disarm,
            patch.object(close_module.guard, "prune"),
        ):
            self.assertEqual(close_module.close_workspace("feat", path), 0)

        self.assertEqual(calls, [("workspace", "list"), ("workspace", "close", "w3")])
        disarm.assert_called_once_with("w3")

    def test_path_mismatch_does_not_fall_back_to_colliding_label(self):
        calls = []
        requested = Path("/repos/one/workspaces/feature-auth")

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {
                            "label": "feature-auth",
                            "workspace_id": "other",
                            "worktree": {
                                "checkout_path": "/repos/two/workspaces/feature-auth"
                            },
                        }
                    ]
                }
            return {}

        with (
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(close_module.guard, "disarm") as disarm,
            patch.object(close_module.guard, "prune"),
        ):
            self.assertEqual(close_module.close_workspace("feature/auth", requested), 0)

        self.assertNotIn(("workspace", "close", "other"), calls)
        disarm.assert_not_called()

    def test_path_mismatch_does_not_close_hyphen_workspace_for_slash_name(self):
        calls = []
        requested = Path("/repos/one/feature-auth-abc")

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [
                        {
                            "label": "feature-auth",
                            "workspace_id": "hyphen",
                            "worktree": {"checkout_path": "/repos/one/feature-auth"},
                        }
                    ]
                }
            return {}

        with (
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(close_module.guard, "disarm"),
            patch.object(close_module.guard, "prune"),
        ):
            self.assertEqual(close_module.close_workspace("feature/auth", requested), 0)

        self.assertNotIn(("workspace", "close", "hyphen"), calls)

    def test_noop_when_workspace_absent(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            return {"workspaces": []}

        with (
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(close_module.guard, "disarm") as disarm,
            patch.object(close_module.guard, "prune") as prune,
        ):
            self.assertEqual(close_module.close_workspace("ghost"), 0)

        self.assertEqual(calls, [("workspace", "list")])
        disarm.assert_not_called()
        prune.assert_called_once_with(set())


class RestoreFocusTests(unittest.TestCase):
    def run_close(self, before_id, after_id, primary=None):
        calls = []
        lists = [
            [
                {"label": "caller", "workspace_id": "w1", "focused": before_id == "w1"},
                {"label": "plugin", "workspace_id": "w2", "focused": before_id == "w2"},
                {
                    "label": "main",
                    "workspace_id": "w3",
                    "focused": before_id == "w3",
                    "worktree": {"checkout_path": "/repo/main"},
                },
            ],
            [
                {"label": "caller", "workspace_id": "w1", "focused": after_id == "w1"},
                {
                    "label": "main",
                    "workspace_id": "w3",
                    "focused": after_id == "w3",
                    "worktree": {"checkout_path": "/repo/main"},
                },
            ],
        ]

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": lists.pop(0)}
            return {}

        with (
            patch.object(herdr_module, "herdr", side_effect=fake_herdr),
            patch.object(close_module.guard, "disarm"),
            patch.object(close_module.guard, "prune"),
        ):
            close_module.close_workspace("plugin", primary=primary)
        return calls

    def focus_calls(self, calls):
        return [c for c in calls if c[:2] == ("workspace", "focus")]

    def test_restores_previous_workspace_when_close_moves_focus(self):
        calls = self.run_close(
            before_id="w1", after_id="w3", primary=Path("/repo/main")
        )
        self.assertEqual(self.focus_calls(calls), [("workspace", "focus", "w1")])

    def test_lands_on_primary_when_closed_workspace_was_focused(self):
        calls = self.run_close(
            before_id="w2", after_id="w1", primary=Path("/repo/main")
        )
        self.assertEqual(self.focus_calls(calls), [("workspace", "focus", "w3")])

    def test_no_refocus_when_focus_unmoved(self):
        calls = self.run_close(
            before_id="w1", after_id="w1", primary=Path("/repo/main")
        )
        self.assertEqual(self.focus_calls(calls), [])

    def test_no_refocus_without_primary_match(self):
        calls = self.run_close(
            before_id="w2", after_id="w1", primary=Path("/elsewhere")
        )
        self.assertEqual(self.focus_calls(calls), [])


if __name__ == "__main__":
    unittest.main()
