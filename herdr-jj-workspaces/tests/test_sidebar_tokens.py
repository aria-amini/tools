"""sidebar_tokens: split diffstat and PR tokens for the herdr sidebar."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from herdr_jj.lib import sidebar
from herdr_jj.lib.jj import JjError


@pytest.fixture
def fake_repo(monkeypatch, tmp_path):
    """Patch jj and dashboard lookups; delta counts inject per test."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def make(*, name="side", delta=(0, 0), prs=None, bookmarks=None):
        monkeypatch.setattr(
            sidebar,
            "current_workspace",
            lambda cwd: SimpleNamespace(name=name, root=repo),
        )
        def fake_jj(*args, cwd=None, **kwargs):
            return "tip" if args[-1] == "commit_id" else ""

        monkeypatch.setattr(sidebar, "jj", fake_jj)
        monkeypatch.setattr(
            sidebar, "read_bookmarks", lambda root: {"main": {"": "m1"}}
        )
        monkeypatch.setattr(sidebar, "resolve_trunk", lambda root, b: ("main", "m1"))
        monkeypatch.setattr(sidebar, "_delta", lambda cwd, trunk, tip: delta)
        if bookmarks is None:
            bookmarks = {} if name == "default" else {"side": ("side", "at tip", "")}
        monkeypatch.setattr(
            sidebar,
            "bookmark_details",
            lambda root, n, t, b=None: bookmarks.get(n, ("", "", "")),
        )
        monkeypatch.setattr(sidebar, "repo_prs", lambda root, **k: prs or {})

    return make


class TestSidebarTokens:
    def test_clean_stack_reports_only_status(self, fake_repo):
        fake_repo()
        assert sidebar.sidebar_tokens(Path("/ws")) == {"jj_status": ""}

    def test_delta_splits_into_added_and_removed(self, fake_repo):
        fake_repo(delta=(3, 1))
        assert sidebar.sidebar_tokens(Path("/ws")) == {
            "jj_status": "+3-1",
            "added": "+3",
            "removed": "-1",
        }

    def test_zero_side_omits_its_token(self, fake_repo):
        fake_repo(delta=(5, 0))
        assert sidebar.sidebar_tokens(Path("/ws")) == {
            "jj_status": "+5-0",
            "added": "+5",
        }

    def test_open_pr_token_has_no_mark(self, fake_repo):
        fake_repo(prs={"side": ("#12", "OPEN")})
        assert sidebar.sidebar_tokens(Path("/ws")) == {
            "jj_status": "#12",
            "pr": "#12",
        }

    def test_merged_pr_token_carries_check(self, fake_repo):
        fake_repo(prs={"side": ("#7", "MERGED")})
        assert sidebar.sidebar_tokens(Path("/ws"))["pr"] == "#7 ✓"

    def test_closed_pr_token_carries_cross(self, fake_repo):
        fake_repo(prs={"side": ("#7", "CLOSED")})
        assert sidebar.sidebar_tokens(Path("/ws"))["pr"] == "#7 ✗"

    def test_default_workspace_falls_back_to_trunk_branch(self, fake_repo):
        fake_repo(name="default")
        assert sidebar.sidebar_tokens(Path("/ws"))["branch"] == "main"

    def test_branch_hidden_when_it_equals_workspace_name(self, fake_repo):
        fake_repo(name="feat", bookmarks={"feat": ("feat", "at tip", "")})
        assert "branch" not in sidebar.sidebar_tokens(Path("/ws"))

    def test_branch_shown_when_workspace_name_differs(self, fake_repo):
        fake_repo(name="feat", bookmarks={"feat": ("side", "at tip", "")})
        assert sidebar.sidebar_tokens(Path("/ws"))["branch"] == "side"

    def test_default_workspace_keeps_trunk_branch(self, fake_repo):
        fake_repo(name="default", delta=(2, 1))
        tokens = sidebar.sidebar_tokens(Path("/ws"))
        assert tokens["branch"] == "main"
        assert tokens["added"] == "+2"

    def test_no_pr_for_bookmark_without_one(self, fake_repo):
        fake_repo(prs={"other": ("#3", "OPEN")})
        assert "pr" not in sidebar.sidebar_tokens(Path("/ws"))

    def test_trunk_failure_returns_only_empty_status(self, fake_repo, monkeypatch):
        fake_repo()

        def fail(*a, **k):
            raise sidebar.DashboardError("no trunk")

        monkeypatch.setattr(sidebar, "resolve_trunk", fail)
        assert sidebar.sidebar_tokens(Path("/ws")) == {"jj_status": ""}


class TestDeltaParsing:
    def test_parses_insertions_and_deletions(self, monkeypatch, tmp_path):
        stat = "file.txt | 4 +++-\n1 file changed, 3 insertions(+), 1 deletion(-)"

        def fake_jj(*args, cwd=None, **kwargs):
            if args[0] == "diff":
                return stat
            return "base" if args[4].startswith("fork_point") else "x"

        monkeypatch.setattr(sidebar, "jj", fake_jj)
        assert sidebar._delta(tmp_path, "main", "tip") == (3, 1)

    def test_jj_error_yields_zero_counts(self, monkeypatch, tmp_path):
        def fail(*args, **kwargs):
            raise JjError("boom")

        monkeypatch.setattr(sidebar, "jj", fail)
        assert sidebar._delta(tmp_path, "main", "tip") == (0, 0)
