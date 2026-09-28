from pathlib import Path

import pytest

from herdr_jj import dashboard_data as data
from herdr_jj import finish


@pytest.fixture
def repo(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        '[user]\nname="Test"\nemail="test@example.com"\n'
        '[revset-aliases]\n"immutable_heads()"="root()"\n'
    )
    monkeypatch.setenv("JJ_CONFIG", str(config))
    root = tmp_path / "repo"
    data.command("jj", "git", "init", str(root))
    (root / "shared").write_text("base\n")
    data.command("jj", "describe", "-m", "base", cwd=root)
    data.command("jj", "bookmark", "create", "main", cwd=root)
    data.command("jj", "new", cwd=root)
    monkeypatch.setattr(finish, "live_state", lambda: ([], []))
    return root


def branch(repo, name="feature"):
    root = repo.parent / name
    data.command(
        "jj", "workspace", "add", str(root), "--name", name, "-r", "main", cwd=repo
    )
    (root / name).write_text("work\n")
    data.command("jj", "describe", "-m", name, cwd=root)
    return root


def advance(repo):
    (repo / "upstream").write_text("upstream\n")
    data.command("jj", "describe", "-m", "upstream", cwd=repo)
    data.command("jj", "bookmark", "move", "main", cwd=repo)
    data.command("jj", "new", cwd=repo)


def test_fast_forward_skips_empty_working_copy(repo):
    root = branch(repo)
    tip = data.single(root, "@")
    data.command("jj", "new", cwd=root)
    plan = finish.plan_merge(root)
    assert not plan.rebase
    assert plan.tip == tip
    finish.merge(plan)
    assert data.single(repo, "main") == tip
    assert root.is_dir()


def test_bookmark_relationships(repo):
    root = branch(repo)
    tip = data.single(root, "@")
    assert data.bookmark_details(repo, "feature", tip) == ("", "no bookmark", "")
    data.command("jj", "bookmark", "create", "feature", "-r", "main", cwd=repo)
    assert data.bookmark_details(repo, "feature", tip) == (
        "feature",
        f"{data.AHEAD}1",
        "local only",
    )
    data.command("jj", "bookmark", "set", "feature", "-r", tip, cwd=repo)
    assert data.bookmark_details(repo, "feature", tip) == (
        "feature",
        "at tip",
        "local only",
    )
    base = data.single(repo, "main")
    assert data.bookmark_details(repo, "feature", base)[1] == f"{data.BEHIND}1"
    advance(repo)
    assert data.bookmark_details(repo, "feature", data.single(repo, "main"))[1] == (
        f"{data.AHEAD}1 {data.BEHIND}1"
    )


@pytest.mark.parametrize("trunk", ["main", "master", "develop"])
def test_default_workspace_uses_trunk_bookmark(repo, monkeypatch, trunk):
    if trunk != "main":
        data.command("jj", "bookmark", "rename", "main", trunk, cwd=repo)
    if trunk == "develop":
        data.command(
            "jj", "config", "set", "--repo", "herdr-jj.trunk-bookmark", trunk, cwd=repo
        )
    monkeypatch.setattr(
        data,
        "live_state",
        lambda: (
            [{"workspace_id": "w1", "worktree": {"checkout_path": str(repo)}}],
            [],
        ),
    )
    snapshot = data.collect()
    assert not snapshot.errors
    row = next(row for row in snapshot.rows if row.is_primary)
    assert not row.error
    assert row.bookmark == trunk
    assert row.bookmark_state == "at tip"


def test_rebase_preserves_stack_and_moves_target(repo):
    root = branch(repo)
    advance(repo)
    plan = finish.plan_merge(root)
    assert plan.rebase
    finish.merge(plan)
    assert data.single(root, "main") == data.single(root, "@")
    assert (root / "upstream").read_text() == "upstream\n"
    assert (root / "feature").read_text() == "work\n"


def test_clean_workspace_collection_batches_reads(repo, monkeypatch, tmp_path):
    for name in ("one", "two", "three"):
        data.command(
            "jj",
            "workspace",
            "add",
            str(repo.parent / name),
            "--name",
            name,
            "-r",
            "main",
            cwd=repo,
        )
    data.command("jj", "bookmark", "create", "one", "-r", "main", cwd=repo)
    sessions = [
        {"workspace_id": "w0", "worktree": {"checkout_path": str(repo)}},
        {"workspace_id": "w1", "worktree": {"checkout_path": str(repo.parent / "one")}},
        {"workspace_id": "w2", "worktree": {"checkout_path": str(repo.parent / "two")}},
        {
            "workspace_id": "w3",
            "worktree": {"checkout_path": str(repo.parent / "three")},
        },
    ]
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(data, "live_state", lambda: (sessions, []))
    original = data.command
    calls = []

    def execute(*args, **kwargs):
        calls.append(args)
        if args[0] == "gh":
            return (
                '[{"number":12,"state":"OPEN","headRefName":"one"},'
                '{"number":9,"state":"MERGED","headRefName":"main"}]'
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(data, "command", execute)
    snapshot = data.collect()
    assert not snapshot.errors
    assert len(snapshot.rows) == 4
    assert all(
        not row.error and row.ahead == 0 and row.delta == "+0 −0"
        for row in snapshot.rows
    )
    assert len(calls) <= 4
    one = next(row for row in snapshot.rows if row.name == "one")
    assert one.pr == "#12" and one.pr_state == "OPEN"
    primary = next(row for row in snapshot.rows if row.is_primary)
    assert primary.pr == "" and primary.pr_state == ""

    (repo / "change").write_text("new work\n")
    original("jj", "describe", "-m", "new work", cwd=repo)
    fresh = data.collect()
    primary = next(row for row in fresh.rows if row.is_primary)
    assert primary.ahead == 1
    assert primary.delta == "+1 −0"
    assert primary.bookmark_state == f"{data.AHEAD}1"


def test_repo_prs_cache_hits_and_failures(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    calls = []
    payload = '[{"number":5,"state":"OPEN","headRefName":"feat"}]'

    def execute(*args, **kwargs):
        calls.append(args)
        if args[0] == "gh":
            return payload
        raise AssertionError(f"unexpected command {args}")

    monkeypatch.setattr(data, "command", execute)
    first = data.repo_prs(repo)
    assert first == {"feat": ("#5", "OPEN")}
    second = data.repo_prs(repo)
    assert second == first
    assert len(calls) == 1

    monkeypatch.setattr(
        data,
        "command",
        lambda *args, **kwargs: (_ for _ in ()).throw(data.DashboardError("no gh")),
    )
    for cached in (tmp_path / "state").glob("pr-cache-*.json"):
        cached.unlink()
    assert data.repo_prs(repo) == {}
    assert data.repo_prs(repo) == {}


def test_squash_merge_collapses_stack(repo):
    root = branch(repo)
    data.command("jj", "new", cwd=root)
    (root / "more").write_text("more\n")
    data.command("jj", "describe", "-m", "more", cwd=root)
    advance(repo)
    plan = finish.plan_merge(root)
    assert len(plan.commits) == 2
    assert plan.rebase
    result = finish.squash_merge(plan)
    assert "Squashed 2 commits" in result
    assert len(data.revisions(repo, "bookmarks(exact:main)")) == 1
    # The primary checkout is stale after the rewrite; assert on the commit.
    files = data.read_jj(repo, "file", "list", "-r", "bookmarks(exact:main)")
    assert "feature" in files and "more" in files
    assert not data.revisions(root, "main..@ & ~empty()")
    with pytest.raises(data.DashboardError, match="No commits ahead"):
        finish.plan_merge(root)


def test_squash_merge_rejects_immutable_trunk(repo):
    root = branch(repo)
    data.command(
        "jj",
        "config",
        "set",
        "--repo",
        'revset-aliases."immutable_heads()"',
        "root() | main | (working_copies() ~ @)",
        cwd=repo,
    )
    plan = finish.plan_merge(root)
    with pytest.raises(data.DashboardError, match="immutable"):
        finish.squash_merge(plan)


def test_push_pr_and_commit_commands(repo, monkeypatch):
    root = branch(repo)
    data.command("jj", "bookmark", "create", "feature", "-r", "main", cwd=repo)
    calls = []
    real = finish.command

    def execute(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("gh", "pr"):
            return "https://example.test/pr/17\n"
        if args[:2] == ("jj", "git"):
            return ""
        return real(*args, **kwargs)

    monkeypatch.setattr(finish, "command", execute)
    monkeypatch.setattr(finish, "repo_prs", lambda repo, env=None, ttl=120: {})
    assert "Pushed feature" in finish.push_bookmark(root, "feature")
    assert finish.create_pr(root, "feature", "main") == "https://example.test/pr/17"
    assert "new change" in finish.commit(root, "add work")
    assert ("jj", "git", "push", "--bookmark", "feature") in calls
    assert (
        "gh",
        "pr",
        "create",
        "--fill",
        "--head",
        "feature",
        "--base",
        "main",
    ) in calls
    assert ("jj", "commit", "-m", "add work") in calls


def test_duplicate_herdr_workspaces_merge_per_checkout(repo, monkeypatch):
    root = branch(repo)
    sessions = [
        {"workspace_id": "w1", "worktree": {"checkout_path": str(root)}},
        {"workspace_id": "w2", "worktree": {"checkout_path": str(root)}},
    ]
    monkeypatch.setattr(data, "live_state", lambda: (sessions, []))
    snapshot = data.collect()
    assert not snapshot.errors
    rows = [row for row in snapshot.rows if row.root == root]
    assert len(rows) == 1
    assert rows[0].sessions == ("w1", "w2")


def test_stale_pane_cwds_are_ignored(repo, monkeypatch):
    sessions = [{"workspace_id": "w1", "worktree": {"checkout_path": str(repo)}}]
    panes = [
        {
            "workspace_id": "w9",
            "pane_id": "p1",
            "cwd": "/nonexistent/deleted (deleted)",
        }
    ]
    monkeypatch.setattr(data, "live_state", lambda: (sessions, panes))
    snapshot = data.collect()
    assert not snapshot.errors
    assert [row.root for row in snapshot.rows] == [repo]


def test_conflict_in_stack_never_advances_target(repo):
    root = branch(repo)
    (root / "shared").write_text("feature\n")
    data.command("jj", "describe", "-m", "feature", cwd=root)
    data.command("jj", "new", "-m", "child", cwd=root)
    (root / "child").write_text("child\n")
    data.command("jj", "status", cwd=root)
    (repo / "shared").write_text("trunk\n")
    advance(repo)
    trunk = data.single(repo, "main")
    plan = finish.plan_merge(root)
    with pytest.raises(data.DashboardError, match="conflicts"):
        finish.merge(plan)
    assert data.single(repo, "main") == trunk
    assert root.exists()
    data.command("jj", "status", cwd=root)


def test_immutable_stack_rebase_is_rejected(repo):
    root = branch(repo)
    data.command("jj", "bookmark", "create", "protected", cwd=root)
    data.command(
        "jj",
        "config",
        "set",
        "--repo",
        'revset-aliases."immutable_heads()"',
        "root() | protected",
        cwd=repo,
    )
    advance(repo)
    before = data.single(root, "protected")
    with pytest.raises(data.DashboardError, match="immutable"):
        finish.plan_merge(root)
    assert data.single(root, "protected") == before


def test_shared_descendant_rebase_is_rejected(repo):
    root = branch(repo)
    tip = data.single(root, "@")
    data.command("jj", "new", tip, "-m", "other work", cwd=repo)
    data.command("jj", "new", "main", cwd=repo)
    advance(repo)
    with pytest.raises(data.DashboardError, match="Other work"):
        finish.plan_merge(root)


def test_changed_workspace_invalidates_confirmation(repo):
    root = branch(repo)
    plan = finish.plan_merge(root)
    (root / "later").write_text("later\n")
    with pytest.raises(data.DashboardError, match="changed"):
        finish.merge(plan)
    assert data.single(repo, "main") == plan.trunk


def test_changed_target_invalidates_confirmation(repo):
    root = branch(repo)
    plan = finish.plan_merge(root)
    advance(repo)
    with pytest.raises(data.DashboardError, match="changed"):
        finish.merge(plan)


def test_active_agent_and_unmerged_cleanup_are_rejected(repo, monkeypatch):
    root = branch(repo)
    with pytest.raises(data.DashboardError, match="unmerged"):
        finish.cleanup(root)
    monkeypatch.setattr(
        finish,
        "live_state",
        lambda: (
            [{"workspace_id": "w1"}],
            [
                {
                    "workspace_id": "w1",
                    "pane_id": "p1",
                    "cwd": str(root),
                    "agent": "opencode",
                    "agent_status": "working",
                }
            ],
        ),
    )
    with pytest.raises(data.DashboardError, match="not idle"):
        finish.plan_merge(root)


def test_jw_refusal_never_removes_directory(repo, monkeypatch):
    root = branch(repo)
    finish.merge(finish.plan_merge(root))
    real = finish.command

    def refuse(*args, **kwargs):
        if args[0] == "jw":
            raise data.DashboardError("jw refused")
        return real(*args, **kwargs)

    monkeypatch.setattr(finish, "command", refuse)
    with pytest.raises(data.DashboardError, match="jw refused"):
        finish.cleanup(root)
    assert root.is_dir()
    assert "feature" in data.read_jj(repo, "workspace", "list")


def test_real_jw_merge_cleanup_retains_bookmark(repo):
    data.command(
        "jw",
        "add",
        "managed",
        "--at",
        "main",
        "--bookmark",
        "managed",
        "--no-links",
        cwd=repo,
    )
    root = Path(data.command("jw", "path", "managed", cwd=repo).strip())
    (root / "managed").write_text("managed\n")
    data.command("jj", "describe", "-m", "managed", cwd=root)
    tip = data.single(root, "@")
    finish.merge(finish.plan_merge(root))
    finish.cleanup(root)
    assert not root.exists()
    assert data.single(repo, "main") == tip
    assert data.revisions(repo, 'bookmarks(exact:"managed")')


def test_merge_with_immutable_main(repo):
    root = branch(repo)
    data.command(
        "jj",
        "config",
        "set",
        "--repo",
        'revset-aliases."immutable_heads()"',
        "root() | main | (working_copies() ~ @)",
        cwd=repo,
    )
    plan = finish.plan_merge(root)
    finish.merge(plan)
    assert data.single(repo, "main") == plan.tip
    data.command("jj", "status", cwd=root)


def test_ambiguous_trunk_requires_configuration(repo):
    root = branch(repo)
    data.command("jj", "bookmark", "create", "master", "-r", "main", cwd=repo)
    with pytest.raises(data.DashboardError, match="trunk-bookmark"):
        finish.plan_merge(root)
    data.command(
        "jj", "config", "set", "--repo", "herdr-jj.trunk-bookmark", "main", cwd=repo
    )
    assert finish.plan_merge(root).bookmark == "main"


def test_undescribed_work_and_primary_merge_rejected(repo):
    root = branch(repo)
    data.command("jj", "describe", "-m", "", cwd=root)
    with pytest.raises(data.DashboardError, match="Describe"):
        finish.plan_merge(root)
    with pytest.raises(data.DashboardError, match="secondary"):
        finish.plan_merge(repo)


def test_changed_session_id_never_closes_unrelated_pane(repo, monkeypatch):
    root = branch(repo)
    finish.merge(finish.plan_merge(root))
    snapshots = iter(
        [
            (
                [{"workspace_id": "w1"}],
                [
                    {
                        "workspace_id": "w1",
                        "pane_id": "p1",
                        "terminal_id": "original",
                        "cwd": str(root),
                    }
                ],
            ),
            (
                [{"workspace_id": "w1"}],
                [
                    {
                        "workspace_id": "w1",
                        "pane_id": "p1",
                        "terminal_id": "original",
                        "cwd": str(root),
                    }
                ],
            ),
            (
                [{"workspace_id": "w1"}],
                [
                    {
                        "workspace_id": "w1",
                        "pane_id": "p1",
                        "terminal_id": "unrelated",
                        "cwd": str(repo),
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(finish, "live_state", lambda: next(snapshots))
    real = finish.command
    calls = []

    def execute(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("jw", "path"):
            return str(root)
        if args[:2] == ("jw", "remove"):
            import shutil

            shutil.rmtree(root)
            return ""
        return real(*args, **kwargs)

    monkeypatch.setattr(finish, "command", execute)
    finish.cleanup(root)
    assert not any(call[:3] == ("herdr", "workspace", "close") for call in calls)


def test_cross_repo_discovery_agents_missing_and_readonly(repo, monkeypatch):
    root = branch(repo)
    other = repo.parent / "other"
    data.command("jj", "git", "init", str(other))
    data.command("jj", "bookmark", "create", "main", cwd=other)
    panes = [
        {
            "workspace_id": "w1",
            "pane_id": f"p{i}",
            "terminal_id": f"t{i}",
            "cwd": str(root),
            "agent": "opencode",
            "agent_status": state,
        }
        for i, state in enumerate(("working", "blocked"))
    ] + [{"workspace_id": "w2", "pane_id": "p9", "cwd": str(other)}]
    monkeypatch.setattr(data, "live_state", lambda: ([{"workspace_id": "w1"}], panes))
    monkeypatch.chdir(repo)
    before = data.single(root, "@")
    (root / "unsnapshotted").write_text("new\n")
    snapshot = data.collect()
    assert not snapshot.errors
    assert {r.repo for r in snapshot.rows} == {repo, other}
    row = next(r for r in snapshot.rows if r.root == root)
    assert len(row.agents) == 2
    assert row.status == "blocked"
    assert data.single(root, "@") == before
