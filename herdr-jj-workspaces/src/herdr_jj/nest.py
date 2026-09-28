"""Attach herdr worktree provenance to jj workspaces created outside this plugin.

jj-waltz opens its workspaces with a plain workspace create, so they land flat
in the sidebar. Nesting is idempotent: sessions that already carry worktree
metadata, primary checkouts, and non-jj directories are skipped.
"""

import argparse
import sys
from pathlib import Path

from .dashboard_data import (
    DashboardError,
    checkout,
    command,
    live_state,
    repository,
)
from .lib.herdr import herdr_bin


def nest_sessions() -> list[str]:
    sessions, panes = live_state()
    pane_cwds: dict[str, str] = {}
    for pane in panes:
        workspace_id = pane.get("workspace_id")
        cwd = pane.get("cwd")
        if workspace_id and cwd and workspace_id not in pane_cwds:
            pane_cwds[workspace_id] = cwd

    nested: list[str] = []
    for session in sessions:
        if session.get("worktree"):
            continue
        workspace_id = session.get("workspace_id")
        cwd = pane_cwds.get(workspace_id or "")
        if not cwd or not Path(cwd).is_dir():
            continue
        root = checkout(Path(cwd))
        if root is None:
            continue
        try:
            repo = repository(root)
        except OSError:
            continue
        if root == repo:
            continue
        try:
            command(
                herdr_bin(),
                "worktree",
                "open",
                "--cwd",
                str(repo),
                "--path",
                str(root),
                "--no-focus",
            )
        except DashboardError as error:
            print(f"herdr-jj nest: {root}: {error}", file=sys.stderr)
            continue
        nested.append(str(root))
    return nested


def run(args: argparse.Namespace) -> int:
    nested = nest_sessions()
    for path in nested:
        print(f"nested {path}")
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "nest", help="attach worktree provenance to un-nested jj workspaces"
    )
    parser.set_defaults(run=run)
