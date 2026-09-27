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
_delta_cache: dict[str, tuple[float, str, str]] = {}


def _delta(cwd: Path, trunk: str, tip: str) -> str:
    key = str(cwd)
    hit = _delta_cache.get(key)
    if hit and hit[1] == tip and time.monotonic() - hit[0] < DELTA_TTL_S:
        return hit[2]
    delta = ""
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
        if match and (int(match[1]) or int(match[2])):
            delta = f"+{match[1]}-{match[2]}"
    except JjError:
        pass
    _delta_cache[key] = (time.monotonic(), tip, delta)
    return delta


def sidebar_token(cwd: Path) -> str:
    """Arrow counts, stack delta, PR — e.g. `↓1 ↑2 +34-7 #12`; empty at rest."""
    cwd = Path(cwd)
    tip = jj("log", "--no-graph", "-r", "@", "-T", "commit_id", cwd=cwd).strip()
    workspace = current_workspace(cwd)
    repo = workspace.root
    try:
        bookmarks = read_bookmarks(repo)
        _, trunk = resolve_trunk(repo, bookmarks)
    except (DashboardError, JjError):
        return ""
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
    delta = _delta(cwd, trunk, tip)
    if delta:
        parts.append(delta)
    bookmark = ""
    if workspace.name != "default":
        try:
            bookmark, _, _ = bookmark_details(repo, workspace.name, tip, bookmarks)
        except DashboardError:
            bookmark = ""
    if bookmark:
        try:
            pr = repo_prs(repo).get(bookmark)
            if pr:
                parts.append(pr[0])
        except DashboardError:
            pass
    return " ".join(parts)
