"""Apply the repo layout to workspaces herdr's built-in flow creates.

prefix+shift+g (`worktree create`) opens a single-pane workspace and emits
worktree.created; this hook splits it and runs the .herdr.toml commands.
`worktree open` emits worktree.opened instead, so herdr-jj's own creation
path (open.py) never re-applies the layout here.
"""

import argparse
import os
import sys
from pathlib import Path

from .adopt import checkout_path, event_payload
from .lib.herdr import HerdrError, find_by_worktree_path, herdr
from .lib.jj import JjError, primary_root
from .lib.layout import apply as apply_layout
from .lib.layout import load as load_layout


def workspace_id(payload: dict) -> str | None:
    value = (payload.get("workspace") or {}).get("workspace_id")
    return value if isinstance(value, str) and value else None


def repo_root(payload: dict, checkout: Path) -> Path:
    """Primary workspace root; non-jj checkouts fall back to themselves."""
    workspaces = payload.get("workspace") or {}
    for value in (
        (workspaces.get("worktree") or {}).get("repo_root"),
        (payload.get("worktree") or {}).get("repo_root"),
    ):
        if isinstance(value, str) and value:
            return Path(value)
    try:
        return primary_root(checkout)
    except JjError:
        return checkout


def run(args: argparse.Namespace) -> int:
    payload = event_payload(os.environ)
    checkout = checkout_path(payload)
    if checkout is None or not checkout.is_dir():
        return 0
    ident = workspace_id(payload)
    if ident is None:
        found = find_by_worktree_path(checkout)
        ident = found["workspace_id"] if found else None
    if ident is None:
        print(f"herdr-jj layout: no herdr workspace for {checkout}", file=sys.stderr)
        return 0
    try:
        # More than one pane means the workspace is already laid out.
        panes = herdr("pane", "list", "--workspace", ident).get("panes", [])
        if len(panes) != 1:
            return 0
        layout = load_layout(repo_root(payload, checkout))
        apply_layout(panes[0]["pane_id"], layout)
    except HerdrError as error:
        print(f"herdr-jj layout: {error}", file=sys.stderr)
        return 1
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "layout",
        help="apply the repo layout to a created worktree workspace (event hook)",
    ).set_defaults(run=run)
