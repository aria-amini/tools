import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from herdr_jj import new as new_module


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    result = Mock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


class NewWorkspaceTests(unittest.TestCase):
    def test_creates_and_opens_with_project_context(self):
        with (
            patch.object(
                new_module.subprocess,
                "run",
                side_effect=[
                    completed(),
                    completed(stdout="/w/repo/feature\n"),
                ],
            ) as run,
            patch.object(
                new_module, "open_workspace", return_value=0
            ) as open_workspace,
        ):
            rc = new_module.new_workspace("feature", "trunk()", Path("/w/repo"))

        self.assertEqual(rc, 0)
        run.assert_any_call(
            ["jw", "add", "feature", "--at", "trunk()"],
            cwd=Path("/w/repo"),
            text=True,
            capture_output=True,
            check=False,
        )
        run.assert_any_call(
            ["jw", "path", "feature"],
            cwd=Path("/w/repo"),
            text=True,
            capture_output=True,
            check=False,
        )
        open_workspace.assert_called_once_with(
            Path("/w/repo/feature"), Path("/w/repo"), workspace_name="feature"
        )

    def test_omits_at_flag_when_base_is_none(self):
        with (
            patch.object(
                new_module.subprocess,
                "run",
                side_effect=[completed(), completed(stdout="/w/repo/feature\n")],
            ) as run,
            patch.object(new_module, "open_workspace", return_value=0),
        ):
            self.assertEqual(
                new_module.new_workspace("feature", None, Path("/w/repo")), 0
            )

        self.assertEqual(run.call_args_list[0].args[0], ["jw", "add", "feature"])

    def test_existing_workspace_still_opens(self):
        with (
            patch.object(
                new_module.subprocess,
                "run",
                side_effect=[
                    completed(returncode=1, stderr="already exists"),
                    completed(stdout="/w/repo/feature\n"),
                ],
            ),
            patch.object(
                new_module, "open_workspace", return_value=0
            ) as open_workspace,
        ):
            self.assertEqual(
                new_module.new_workspace("feature", "main", Path("/w/repo")), 0
            )

        open_workspace.assert_called_once_with(
            Path("/w/repo/feature"), Path("/w/repo"), workspace_name="feature"
        )

    def test_failing_add_and_path_resolution_reports_error(self):
        with (
            patch.object(
                new_module.subprocess,
                "run",
                side_effect=[
                    completed(returncode=1, stderr="boom"),
                    completed(returncode=1, stderr="nope"),
                ],
            ),
            patch.object(new_module, "open_workspace") as open_workspace,
        ):
            self.assertEqual(
                new_module.new_workspace("feature", "main", Path("/w/repo")), 1
            )

        open_workspace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
