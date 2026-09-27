"""Close the herdr workspace for a removed jj workspace.

Invoked by the workspace tool's post-remove hook with the workspace name. A
missing herdr workspace is not an error: the hook runs for every removal,
including workspaces that were never opened in herdr.
"""

import argparse
import sys
from pathlib import Path

from .lib import guard
from .lib.herdr import (
    HerdrError,
    Workspace,
    close_for_jj,
    find_by_worktree_path,
    focus_workspace,
    focused_workspace,
    list_workspaces,
)


def close_workspace(
    name: str, path: Path | None = None, primary: Path | None = None
) -> int:
    items = list_workspaces()
    before = focused_workspace(items)
    existing, workspaces = close_for_jj(name, workspaces=items, path=path)
    live_ids = {w["workspace_id"] for w in workspaces}

    if existing is not None:
        live_ids.discard(existing["workspace_id"])
        try:
            guard.disarm(existing["workspace_id"])
        except OSError as error:
            print(f"herdr-jj close: cd-guard disarm failed: {error}", file=sys.stderr)

    try:
        guard.prune(live_ids)
    except OSError as error:
        print(f"herdr-jj close: cd-guard prune failed: {error}", file=sys.stderr)

    if existing is not None:
        _restore_focus(before, primary)
    return 0


def _restore_focus(before: Workspace | None, primary: Path | None) -> None:
    # herdr picks an arbitrary workspace when a close moves focus. Put focus
    # back where the user was; if they were inside the closed workspace, land
    # on the repo's primary worktree instead. Synchronous with the close, so
    # a focus that didn't move means the user (or herdr) is already settled.
    if before is None:
        return
    workspaces = list_workspaces()
    after = focused_workspace(workspaces)
    if after is not None and after["workspace_id"] == before["workspace_id"]:
        return

    target = None
    if any(w["workspace_id"] == before["workspace_id"] for w in workspaces):
        target = before
    elif primary is not None:
        target = find_by_worktree_path(primary, workspaces)
    if target is None:
        return
    try:
        focus_workspace(target["workspace_id"])
    except HerdrError as error:
        print(f"herdr-jj close: focus restore failed: {error}", file=sys.stderr)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "close", help="close the herdr workspace for a removed jj workspace"
    )
    parser.add_argument("--name", required=True, help="jj workspace name")
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="jj workspace path; matches herdr workspaces by worktree "
        "provenance when their label differs (e.g. herdr-created worktrees)",
    )
    parser.add_argument(
        "--primary",
        type=Path,
        default=None,
        help="repo primary workspace path; focus lands on its herdr workspace "
        "when the closed workspace was focused",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    try:
        return close_workspace(args.name, args.path, args.primary)
    except HerdrError as error:
        print(f"herdr-jj close: {error}", file=sys.stderr)
        return 1
