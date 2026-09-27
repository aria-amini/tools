"""Picker popup: fzf over jj workspaces; focus, open, remove, or create."""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .lib.fzf import fzf_select
from .lib.herdr import find_by_worktree_path, focus_workspace, list_workspaces
from .lib.jj import JjError, primary_root, status_token, workspaces
from .open import open_workspace
from .remove import remove_workspace
from .state import resolve_context
from .wizard import wizard


def picker(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    cwd = Path(resolve_context(env)["cwd"])

    try:
        primary = primary_root(cwd)
    except JjError as error:
        print(f"herdr-jj picker: {error}", file=sys.stderr)
        return 1

    herdr_workspaces = list_workspaces()
    jj_workspaces = workspaces(primary)
    lines = []
    for item in jj_workspaces:
        token = status_token(primary, rev=f"{item.name}@")
        existing = find_by_worktree_path(item.root, herdr_workspaces)
        marker = existing.get("label", "open") if existing else "—"
        lines.append(f"{item.name}\t{token}\t{marker}")

    key, line = fzf_select(
        lines, preview="jj log --color=always -r {1}@", expect=("ctrl-d", "ctrl-n")
    )
    if key == "esc":
        return 0
    if key == "ctrl-n":
        return wizard(env)

    name = line.split("\t")[0]
    path = next(item.root for item in jj_workspaces if item.name == name)

    if key == "ctrl-d":
        return remove_workspace(path, primary, env=env)

    existing = find_by_worktree_path(path, herdr_workspaces)
    if existing is not None:
        focus_workspace(existing["workspace_id"])
        return 0
    return open_workspace(path, primary, name)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("picker", help="pick a jj workspace (popup)")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    return picker()
