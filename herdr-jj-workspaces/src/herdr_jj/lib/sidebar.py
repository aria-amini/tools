"""Sidebar token: commits ahead/behind, stack delta, PR."""

import re
import time
from pathlib import Path

from ..dashboard_data import (
    DashboardError,
    bookmark_details,
    read_bookmarks,
    repo_prs,
    resolve_trunk,
)
from .jj import JjError, current_workspace, jj

DELTA_TTL_S = 30.0
_DELTA = re.compile(r"(\d+) insertions?\(\+\), (\d+) deletions?\(-\)")
_delta_cache: dict[str, tuple[float, str, tuple[int, int]]] = {}
_PR_STATE_MARK = {"MERGED": " ✓", "CLOSED": " ✗"}


def _delta(cwd: Path, trunk: str, tip: str) -> tuple[int, int]:
    """Cached (insertions, deletions) between the trunk fork point and tip."""
    key = str(cwd)
    hit = _delta_cache.get(key)
    if hit and hit[1] == tip and time.monotonic() - hit[0] < DELTA_TTL_S:
        return hit[2]
    counts = (0, 0)
    try:
        base = jj(
            "log",
            "--no-graph",
            "-r",
            f"fork_point({trunk} | {tip})",
            "-T",
            "commit_id",
            cwd=cwd,
        ).strip()
        match = _DELTA.search(
            jj("diff", "--from", base, "--to", tip, "--stat", cwd=cwd)
        )
        if match:
            counts = (int(match[1]), int(match[2]))
    except JjError:
        pass
    _delta_cache[key] = (time.monotonic(), tip, counts)
    return counts


def sidebar_tokens(cwd: Path) -> dict[str, str]:
    """Sidebar token dict: `jj_status` plus `added`, `removed`, and `pr`."""
    cwd = Path(cwd)
    tip = jj("log", "--no-graph", "-r", "@", "-T", "commit_id", cwd=cwd).strip()
    workspace = current_workspace(cwd)
    repo = workspace.root
    try:
        bookmarks = read_bookmarks(repo)
        trunk, trunk_target = resolve_trunk(repo, bookmarks)
    except (DashboardError, JjError):
        return {"jj_status": ""}
    insertions, deletions = _delta(cwd, trunk_target, tip)
    parts = []
    for revset, glyph in ((f"{tip}..{trunk}", "↓"), (f"{trunk}..{tip}", "↑")):
        try:
            count = len(
                jj("log", "--no-graph", "-r", revset, "-T", "", cwd=cwd).splitlines()
            )
        except JjError:
            continue
        if count:
            parts.append(f"{glyph}{count}")
    if insertions or deletions:
        parts.append(f"+{insertions}-{deletions}")
    bookmark = ""
    if workspace.name != "default":
        try:
            bookmark, _, _ = bookmark_details(repo, workspace.name, tip, bookmarks)
        except DashboardError:
            bookmark = ""
    pr_token = ""
    if bookmark:
        try:
            pr = repo_prs(repo).get(bookmark)
            if pr:
                parts.append(pr[0])
                pr_token = pr[0] + _PR_STATE_MARK.get(pr[1], "")
        except DashboardError:
            pass
    tokens = {"jj_status": " ".join(parts)}
    # jj worktree children show the workspace name, which equals the bookmark
    # by the jw convention; a matching branch row would just duplicate it.
    branch = bookmark or trunk
    if branch and branch != workspace.name:
        tokens["branch"] = branch
    if insertions:
        tokens["added"] = f"+{insertions}"
    if deletions:
        tokens["removed"] = f"-{deletions}"
    if pr_token:
        tokens["pr"] = pr_token
    return tokens


def sidebar_token(cwd: Path) -> str:
    """Arrow counts, stack delta, PR — e.g. `↓1 ↑2 +34-7 #12`; empty at rest."""
    return sidebar_tokens(cwd)["jj_status"]
