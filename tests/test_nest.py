from pathlib import Path

import pytest

from herdr_jj import dashboard_data, nest


@pytest.fixture
def quiet_herdr(monkeypatch):
    calls = []

    def fake_command(*args, **kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(nest, "command", fake_command)
    monkeypatch.setattr(nest, "herdr_bin", lambda: "herdr")
    return calls


def test_nests_flat_secondary_and_skips_primary_nested_and_nonjj(
    monkeypatch, tmp_path, quiet_herdr
):
    repo = tmp_path / "repo"
    worktree = tmp_path / "repo.wt"
    repo.mkdir()
    worktree.mkdir()

    def fake_checkout(path):
        path = Path(path)
        return path if path in (repo, worktree) else None

    def fake_repository(root):
        return repo

    monkeypatch.setattr(nest, "checkout", fake_checkout)
    monkeypatch.setattr(nest, "repository", fake_repository)
    monkeypatch.setattr(
        nest,
        "live_state",
        lambda: (
            [
                {"workspace_id": "flat"},
                {
                    "workspace_id": "nested",
                    "worktree": {"checkout_path": str(worktree)},
                },
                {"workspace_id": "primary"},
                {"workspace_id": "shell"},
            ],
            [
                {"workspace_id": "flat", "cwd": str(worktree)},
                {"workspace_id": "primary", "cwd": str(repo)},
                {"workspace_id": "shell", "cwd": str(tmp_path / "shell")},
            ],
        ),
    )

    nested = nest.nest_sessions()

    assert nested == [str(worktree)]
    assert quiet_herdr == [
        (
            "herdr",
            "worktree",
            "open",
            "--cwd",
            str(repo),
            "--path",
            str(worktree),
            "--no-focus",
        )
    ]


def test_worktree_open_failure_does_not_stop_other_nesting(
    monkeypatch, tmp_path, quiet_herdr
):
    repo_a = tmp_path / "ra"
    wt_a = tmp_path / "ra.wt"
    repo_b = tmp_path / "rb"
    wt_b = tmp_path / "rb.wt"
    for directory in (repo_a, wt_a, repo_b, wt_b):
        directory.mkdir()

    def fake_checkout(path):
        path = Path(path)
        return path if path in (wt_a, wt_b) else None

    def fake_repository(root):
        return repo_a if root == wt_a else repo_b

    real_command = nest.command

    def failing_for_a(*args, **kwargs):
        if str(wt_a) in args:
            raise dashboard_data.DashboardError("not a git worktree")
        return real_command(*args, **kwargs)

    monkeypatch.setattr(nest, "command", failing_for_a)
    monkeypatch.setattr(nest, "checkout", fake_checkout)
    monkeypatch.setattr(nest, "repository", fake_repository)
    monkeypatch.setattr(
        nest,
        "live_state",
        lambda: (
            [{"workspace_id": "a"}, {"workspace_id": "b"}],
            [
                {"workspace_id": "a", "cwd": str(wt_a)},
                {"workspace_id": "b", "cwd": str(wt_b)},
            ],
        ),
    )

    nested = nest.nest_sessions()

    assert nested == [str(wt_b)]
