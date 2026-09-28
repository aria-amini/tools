import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from herdr_jj.lib import jj as jj_module
from herdr_jj.lib.jj import JjError


def completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


class TestJjWrapper:
    def test_jj_returns_stdout(self):
        with patch.object(subprocess, "run", return_value=completed("hello\n")) as run:
            assert jj_module.jj("root") == "hello\n"
        assert run.call_args.args[0] == ["jj", "root"]

    def test_jj_passes_cwd(self):
        with patch.object(subprocess, "run", return_value=completed("ok")) as run:
            jj_module.jj("root", cwd=Path("/tmp/x"))
        assert run.call_args.kwargs["cwd"] == Path("/tmp/x")

    def test_jj_raises_on_nonzero_exit(self):
        with (
            patch.object(subprocess, "run", return_value=completed("", 1)),
            pytest.raises(JjError),
        ):
            jj_module.jj("root")


class TestRepoRoot:
    def test_returns_root_path(self):
        with patch.object(jj_module, "jj", return_value="/home/u/dotfiles\n"):
            assert jj_module.repo_root(Path("/home/u/dotfiles/sub")) == Path(
                "/home/u/dotfiles"
            )


class TestPrimaryRoot:
    def test_main_workspace_is_its_own_primary(self, tmp_path):
        repo = tmp_path / "dotfiles"
        (repo / ".jj" / "repo").mkdir(parents=True)
        with patch.object(jj_module, "jj", return_value=f"{repo}\n"):
            assert jj_module.primary_root(tmp_path) == repo

    def test_secondary_workspace_resolves_repo_pointer(self, tmp_path):
        primary = tmp_path / "dotfiles"
        (primary / ".jj" / "repo").mkdir(parents=True)
        secondary = tmp_path / "workspaces" / "dotfiles" / "feat"
        (secondary / ".jj").mkdir(parents=True)
        (secondary / ".jj" / "repo").write_text("../../../../dotfiles/.jj/repo")
        with patch.object(jj_module, "jj", return_value=f"{secondary}\n"):
            assert jj_module.primary_root(tmp_path) == primary


class TestJwWrapper:
    def test_jw_returns_stdout(self):
        with patch.object(subprocess, "run", return_value=completed("ok\n")) as run:
            assert jj_module.jw("add", "feat") == "ok\n"
        assert run.call_args.args[0] == ["jw", "add", "feat"]

    def test_jw_raises_on_nonzero_exit(self):
        with (
            patch.object(subprocess, "run", return_value=completed("", 1)),
            pytest.raises(jj_module.JwError),
        ):
            jj_module.jw("add", "feat")


class TestJwAdd:
    def test_defaults_to_at_working_copy(self):
        with patch.object(jj_module, "jw", return_value="") as mock_jw:
            jj_module.jw_add("feat", cwd=Path("/repo"))
        mock_jw.assert_called_once_with("add", "feat", "--at", "@", cwd=Path("/repo"))

    def test_at_override(self):
        with patch.object(jj_module, "jw", return_value="") as mock_jw:
            jj_module.jw_add("feat", cwd=Path("/repo"), at="trunk()")
        mock_jw.assert_called_once_with(
            "add", "feat", "--at", "trunk()", cwd=Path("/repo")
        )

    def test_at_none_omits_flag(self):
        with patch.object(jj_module, "jw", return_value="") as mock_jw:
            jj_module.jw_add("feat", cwd=Path("/repo"), at=None)
        mock_jw.assert_called_once_with("add", "feat", cwd=Path("/repo"))


class TestJwRemove:
    def test_deletes_bookmark_without_prompting(self):
        with patch.object(jj_module, "jw", return_value="") as mock_jw:
            jj_module.jw_remove("feat", cwd=Path("/repo"))
        mock_jw.assert_called_once_with(
            "remove", "feat", "--delete-bookmark", cwd=Path("/repo")
        )


class TestAbsolutizeRepoPointer:
    def test_rewrites_relative_pointer_as_absolute(self, tmp_path):
        primary = tmp_path / "repo"
        (primary / ".jj" / "repo").mkdir(parents=True)
        dest = tmp_path / "ws" / "feat"
        (dest / ".jj").mkdir(parents=True)
        (dest / ".jj" / "repo").write_text("../../../repo/.jj/repo")
        jj_module.absolutize_repo_pointer(dest)
        assert (dest / ".jj" / "repo").read_text() == str(primary / ".jj" / "repo")

    def test_leaves_absolute_pointer_alone(self, tmp_path):
        dest = tmp_path / "ws" / "feat"
        (dest / ".jj").mkdir(parents=True)
        absolute = str(tmp_path / "repo" / ".jj" / "repo")
        (dest / ".jj" / "repo").write_text(absolute)
        jj_module.absolutize_repo_pointer(dest)
        assert (dest / ".jj" / "repo").read_text() == absolute


class TestWorkspaces:
    def test_parses_name_and_root(self):
        output = "default\t/repo\nfeat\t/workspaces/feat\n"
        with patch.object(jj_module, "jj", return_value=output):
            assert jj_module.workspaces(Path("/repo")) == [
                jj_module.Workspace("default", Path("/repo")),
                jj_module.Workspace("feat", Path("/workspaces/feat")),
            ]


class TestWorkspaceNames:
    def test_parses_default_output(self):
        output = "default: kxyzqptm first\nfeat: abcdefgh (empty) second\n"
        with patch.object(jj_module, "jj", return_value=output) as mock_jj:
            assert jj_module.workspace_names(Path("/repo")) == ["default", "feat"]
        assert mock_jj.call_args.args[:2] == ("workspace", "list")


class TestForgetWorkspace:
    def test_passes_name(self):
        with patch.object(jj_module, "jj", return_value="") as mock_jj:
            jj_module.forget_workspace("feat", cwd=Path("/repo"))
        mock_jj.assert_called_once_with(
            "workspace", "forget", "feat", cwd=Path("/repo")
        )

    def test_prunes_git_worktrees_in_colocated_repo(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with (
            patch.object(jj_module, "jj", return_value=""),
            patch.object(jj_module.subprocess, "run") as mock_run,
        ):
            jj_module.forget_workspace("feat", cwd=tmp_path)
        assert mock_run.call_args_list == [
            (
                (["git", "-C", str(tmp_path), "worktree", "prune"],),
                {"capture_output": True, "check": False},
            ),
            (
                (["git", "-C", str(tmp_path), "branch", "-D", "jj-worktree-feat"],),
                {"capture_output": True, "check": False},
            ),
        ]

    def test_skips_prune_without_git_dir(self, tmp_path):
        with (
            patch.object(jj_module, "jj", return_value=""),
            patch.object(jj_module.subprocess, "run") as mock_run,
        ):
            jj_module.forget_workspace("feat", cwd=tmp_path)
        mock_run.assert_not_called()


class TestStatusToken:
    def test_dirty_workspace(self):
        with patch.object(jj_module, "jj", return_value="kxyzqptm ●\n") as mock_jj:
            assert jj_module.status_token(Path("/repo")) == "kxyzqptm ●"
        assert mock_jj.call_args.args[:3] == ("log", "--no-graph", "-r")
        assert "@" in mock_jj.call_args.args

    def test_empty_workspace(self):
        with patch.object(jj_module, "jj", return_value="kxyzqptm ✓\n"):
            assert jj_module.status_token(Path("/repo")) == "kxyzqptm ✓"

    def test_rev_override(self):
        with patch.object(jj_module, "jj", return_value="abc ●") as mock_jj:
            jj_module.status_token(Path("/repo"), rev="feat@")
        assert "feat@" in mock_jj.call_args.args
