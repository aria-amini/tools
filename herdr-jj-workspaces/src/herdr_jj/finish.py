"""Merge and cleanup are separate stages; a cleanup failure never retries a merge."""

import fcntl
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .dashboard_data import (
    DashboardError,
    checkout,
    command,
    live_paths,
    live_state,
    quote,
    read_jj,
    repo_prs,
    repository,
    revisions,
    single,
    trunk_bookmark,
)


@dataclass(frozen=True)
class MergePlan:
    root: Path
    repo: Path
    workspace: str
    bookmark: str
    trunk: str
    head: str
    tip: str
    commits: tuple[str, ...]
    rebase: bool


def workspace_name(root: Path) -> str:
    output = read_jj(
        root, "workspace", "list", "-T", 'self.name() ++ "\\t" ++ self.root() ++ "\\n"'
    )
    for line in output.splitlines():
        name, path = line.split("\t", 1)
        if Path(path).resolve() == root:
            return name
    raise DashboardError("Workspace registration is missing")


def check_agents(root: Path) -> None:
    sessions, panes = live_state()
    paths = live_paths(sessions, panes)
    for pane in panes:
        path = checkout(Path(pane.get("cwd", "/")))
        if (
            (path == root or paths.get(pane["workspace_id"]) == root)
            and pane.get("agent")
            and pane.get("agent_status") not in ("idle", "done")
        ):
            raise DashboardError(f"Agent {pane['pane_id']} is not idle or done")


def plan_merge(root: Path) -> MergePlan:
    root = root.resolve()
    repo = repository(root)
    if root == repo:
        raise DashboardError("Select a secondary workspace to merge")
    check_agents(root)
    # Snapshot only on an explicit action, never on a dashboard refresh.
    command("jj", "status", cwd=root)
    actual = command("jj", "root", cwd=root).strip()
    if Path(actual).resolve() != root:
        raise DashboardError("Workspace path changed")
    workspace = workspace_name(root)
    bookmark = trunk_bookmark(root)
    trunk = single(root, f"bookmarks(exact:{quote(bookmark)})")
    head = single(root, "@")
    tip = head
    if revisions(root, f'{head} & empty() & description(exact:"")'):
        tip = single(root, f"{head}-")
    stack = f"{trunk}..{tip}"
    commits = revisions(root, stack)
    if not commits:
        raise DashboardError(
            "No commits ahead of the target bookmark; use cleanup instead"
        )
    if revisions(root, f"({stack}) & conflicts()"):
        raise DashboardError("The stack contains conflicts")
    if revisions(root, f'({stack}) & description(exact:"")'):
        raise DashboardError("Describe each commit before merge")
    rebase = not revisions(root, f"{trunk} & ::{tip}")
    if rebase:
        affected = f"({trunk}..{head})::"
        if revisions(root, f"({affected}) & immutable()"):
            raise DashboardError(
                "Rebase would rewrite immutable commits; merge manually with a descendant commit"
            )
        if revisions(root, f"({affected}) ~ ({trunk}..{head})"):
            raise DashboardError(
                "Other work descends from this stack; rebase it explicitly first"
            )
        if revisions(root, f"({trunk}..{head}) & (working_copies() ~ @)"):
            raise DashboardError("Another workspace shares this stack")
    return MergePlan(root, repo, workspace, bookmark, trunk, head, tip, commits, rebase)


@contextmanager
def repo_lock(repo: Path):
    lock = repo / ".jj" / "herdr-jj-finish.lock"
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DashboardError(
                "Another dashboard action is active in this repository"
            ) from error
        yield


def merge(plan: MergePlan) -> str:
    with repo_lock(plan.repo):
        if plan_merge(plan.root) != plan:
            raise DashboardError("Workspace or target changed; review a new merge plan")
        tip = plan.tip
        if plan.rebase:
            change = read_jj(
                plan.root, "log", "--no-graph", "-r", plan.tip, "-T", "change_id"
            ).strip()
            command(
                "jj",
                "--ignore-working-copy",
                "rebase",
                "-s",
                f"roots({plan.trunk}..{plan.head})",
                "-d",
                plan.trunk,
                cwd=plan.root,
                timeout=120,
            )
            tip = single(plan.root, change)
            expected_head = single(plan.root, "@")
            command("jj", "workspace", "update-stale", cwd=plan.root)
            if single(plan.root, "@") != expected_head:
                raise DashboardError(
                    "Workspace changed during rebase; target is unchanged"
                )
        if revisions(plan.root, f"({plan.trunk}..{tip}) & conflicts()"):
            raise DashboardError(
                "Rebase produced conflicts. Target is unchanged; resolve them in the workspace"
            )
        check_agents(plan.root)
        expected_head = single(plan.root, "@") if plan.rebase else plan.head
        command("jj", "status", cwd=plan.root)
        if single(plan.root, "@") != expected_head:
            raise DashboardError("Workspace changed during merge; target is unchanged")
        if single(plan.root, f"bookmarks(exact:{quote(plan.bookmark)})") != plan.trunk:
            raise DashboardError(
                "Target moved during merge; workspace remains available"
            )
        # --from makes a concurrent target move a no-op, never a backward move.
        command(
            "jj",
            "--ignore-working-copy",
            "bookmark",
            "move",
            f"exact:{plan.bookmark}",
            "--from",
            plan.trunk,
            "--to",
            tip,
            cwd=plan.root,
        )
        if single(plan.root, f"bookmarks(exact:{quote(plan.bookmark)})") != tip:
            raise DashboardError("Target changed concurrently; no cleanup occurred")
        return f"Merged {len(plan.commits)} commits into {plan.bookmark}. Workspace retained; R cleans it up."


def squash_merge(plan: MergePlan) -> str:
    with repo_lock(plan.repo):
        if plan_merge(plan.root) != plan:
            raise DashboardError("Workspace or target changed; review a new merge plan")
        if revisions(plan.root, f"{plan.trunk} & immutable()"):
            raise DashboardError(
                "Squash rewrites the target commit and it is immutable; use the rebase strategy"
            )
        command(
            "jj",
            "--ignore-working-copy",
            # -u keeps the trunk description; without it jj opens an editor.
            "squash",
            "--from",
            f"{plan.trunk}..{plan.tip}",
            "--into",
            plan.bookmark,
            "--use-destination-message",
            cwd=plan.root,
            timeout=120,
        )
        new_trunk = single(plan.root, f"bookmarks(exact:{quote(plan.bookmark)})")
        if new_trunk == plan.trunk:
            raise DashboardError("Squash did not change the target; nothing merged")
        if revisions(plan.root, f"{new_trunk} & conflicts()"):
            raise DashboardError(
                "Squash produced conflicts on the target; recover with jj op undo"
            )
        command("jj", "workspace", "update-stale", cwd=plan.root)
        return (
            f"Squashed {len(plan.commits)} commits into one on {plan.bookmark}. "
            "Workspace retained; R cleans it up."
        )


@dataclass(frozen=True)
class DiffFile:
    path: str
    lines: int
    bar: str


@dataclass(frozen=True)
class DiffStat:
    files: list[DiffFile]
    total_adds: int = 0
    total_dels: int = 0


def working_copy_summary(root: Path) -> tuple[DiffStat, str]:
    """Files the commit would create, with per-file churn, plus its description."""
    root = root.resolve()
    stat = DiffStat([])
    for line in read_jj(root, "diff", "--stat", snapshot=True).rstrip().splitlines():
        line = line.rstrip()
        total = re.fullmatch(
            r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?"
            r"(?:, (\d+) deletions?\(-\))?",
            line,
        )
        if total:
            stat = DiffStat(
                stat.files,
                int(total.group(2) or 0),
                int(total.group(3) or 0),
            )
            continue
        path, sep, rest = line.rpartition(" | ")
        if not sep:
            continue
        path = path.strip()
        if rest.startswith("Bin"):
            stat.files.append(DiffFile(path, 0, rest.strip()))
            continue
        count, _, bar = rest.strip().partition(" ")
        if not count.isdigit():
            continue
        stat.files.append(DiffFile(path, int(count), bar.strip()))
    stat.files.sort(key=lambda f: f.lines, reverse=True)
    description = read_jj(
        root, "log", "--no-graph", "-r", "@", "-T", "description"
    ).strip()
    return stat, description


def commit(root: Path, message: str) -> str:
    root = root.resolve()
    with repo_lock(repository(root)):
        command("jj", "status", cwd=root)
        command("jj", "commit", "-m", message, cwd=root, timeout=120)
    return "Committed the working copy; a new change started"


def _push(root: Path, bookmark: str) -> None:
    # New remote bookmarks are created by default in jj >= 0.23.
    command(
        "jj",
        "git",
        "push",
        "--bookmark",
        bookmark,
        cwd=root,
        timeout=120,
    )


def push_bookmark(root: Path, bookmark: str) -> str:
    root = root.resolve()
    with repo_lock(repository(root)):
        command("jj", "status", cwd=root)
        single(root, f"bookmarks(exact:{quote(bookmark)})")
        _push(root, bookmark)
    return f"Pushed {bookmark}"


def create_pr(root: Path, bookmark: str, base: str) -> str:
    root = root.resolve()
    repo = repository(root)
    with repo_lock(repo):
        command("jj", "status", cwd=root)
        single(root, f"bookmarks(exact:{quote(bookmark)})")
        _push(root, bookmark)
        url = command(
            "gh",
            "pr",
            "create",
            "--fill",
            "--head",
            bookmark,
            "--base",
            base,
            cwd=root,
            timeout=120,
        ).strip()
    # Refresh the PR column cache so the new PR appears on the next render.
    repo_prs(repo, ttl=0)
    return url


def cleanup(root: Path) -> str:
    with repo_lock(repository(root.resolve())):
        return _cleanup(root)


def _cleanup(root: Path) -> str:
    root = root.resolve()
    repo = repository(root)
    if repo == root:
        raise DashboardError("Cannot remove the primary workspace")
    check_agents(root)
    command("jj", "status", cwd=root)
    trunk = single(root, f"bookmarks(exact:{quote(trunk_bookmark(root))})")
    head = single(root, "@")
    if revisions(
        root, f'({trunk}..{head}) ~ ({head} & empty() & description(exact:""))'
    ):
        raise DashboardError(
            "Workspace contains unmerged commits; merge it before cleanup"
        )
    if not revisions(root, f"{head} & ::{trunk}") and not revisions(
        root, f"{head}- & ::{trunk}"
    ):
        raise DashboardError("Workspace is not based on the target bookmark")
    name = workspace_name(root)
    sessions, panes = live_state()
    ids = {key for key, path in live_paths(sessions, panes).items() if path == root}
    identities = {
        p["terminal_id"]
        for p in panes
        if p["workspace_id"] in ids and p.get("terminal_id")
    }
    if any(
        checkout(Path(p.get("cwd", "/"))) != root
        for p in panes
        if p["workspace_id"] in ids
    ):
        raise DashboardError(
            "Session contains panes outside this workspace; close it manually before cleanup"
        )
    # jw owns directory removal. Never fall back to rmtree after a jw refusal.
    managed_path = command("jw", "path", name, cwd=repo).strip()
    if Path(managed_path).resolve() != root:
        raise DashboardError(
            "jw path differs from the selected checkout; no cleanup occurred"
        )
    command("jw", "remove", name, "--keep-bookmark", cwd=repo, timeout=120)
    if root.exists():
        raise DashboardError(
            "jw left the checkout directory in place; sessions remain open"
        )
    _, current = live_state()
    close_ids = {
        p["workspace_id"] for p in current if p.get("terminal_id") in identities
    }
    from .lib.herdr import herdr_bin

    for session_id in close_ids:
        if any(
            p.get("terminal_id") not in identities
            for p in current
            if p["workspace_id"] == session_id
        ):
            raise DashboardError("Workspace removed; session changed and remains open")
        command(herdr_bin(), "workspace", "close", session_id)
    return f"Removed {name}; bookmarks retained"
