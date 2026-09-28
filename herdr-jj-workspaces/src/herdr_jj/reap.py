"""Reap jj state after herdr removes a worktree checkout.

herdr's worktree remove deletes the checkout directory, then fires
worktree.removed. An adopted jw workspace keeps its jj registration and
bookmark after that; this hook forgets the workspace and deletes the
bookmark, matching `jw remove --delete-bookmark`.

jj drops a workspace's root from its templates once the directory is gone,
so the workspace name is resolved from the adopt ledger first, then from
the jw `<repo>.<name>` sibling path convention.
"""

import argparse
import os
import sys
from pathlib import Path

from . import state
from .adopt import checkout_path, event_payload
from .lib.jj import (
    JjError,
    JwError,
    delete_bookmark,
    forget_workspace,
    jw_remove,
    primary_root,
    workspace_names,
    workspaces,
)


def repo_root_from_payload(payload: dict) -> Path | None:
    provenance = (payload.get("workspace") or {}).get("worktree") or {}
    worktree = payload.get("worktree") or {}
    for value in (provenance.get("repo_root"), worktree.get("repo_root")):
        if value:
            return Path(value)
    return None


def workspace_name_for(path: Path, primary: Path) -> str | None:
    path = Path(path)
    entries = state.read_ledger()
    key = str(path.resolve())
    entry = entries.pop(key, None)
    if entry is not None:
        state.write_ledger(entries)
        name = entry.get("name")
        if isinstance(name, str) and name:
            return name
    prefix = primary.name + "."
    if path.parent.resolve() == primary.parent.resolve() and path.name.startswith(
        prefix
    ):
        return path.name[len(prefix) :]
    return None


def reap_checkout(
    path: Path, primary: Path, name: str, branch: str | None = None
) -> int:
    primary = Path(primary)
    try:
        names = workspace_names(primary)
        known = workspaces(primary)
    except JjError as error:
        print(f"herdr-jj reap: {error}", file=sys.stderr)
        return 1
    # An earlier removal (e.g. the jj remove action) already forgot it.
    if name not in names:
        return 0
    primary_ws = next(
        (item for item in known if item.root.resolve() == primary.resolve()), None
    )
    if primary_ws is not None and primary_ws.name == name:
        print(
            "herdr-jj reap: refusing to forget the primary workspace",
            file=sys.stderr,
        )
        return 1
    try:
        jw_remove(name, cwd=primary)
    except JwError as error:
        # herdr already deleted the checkout directory; jw remove can refuse
        # a workspace whose directory is gone, so clean up in jj directly.
        print(
            f"herdr-jj reap: jw skipped ({error}); forgetting directly",
            file=sys.stderr,
        )
        try:
            forget_workspace(name, cwd=primary)
        except JjError as error:
            print(f"herdr-jj reap: {error}", file=sys.stderr)
            return 1
        delete_bookmark(name, cwd=primary)
    # herdr creates a worktree/<slug> branch per checkout; a colocated repo
    # shows it as a bookmark that nothing else deletes. Only herdr-generated
    # names are touched: a user-supplied --branch can hold real work.
    if branch is not None and branch.startswith("worktree/"):
        delete_bookmark(branch, cwd=primary)
    return 0


def run(args: argparse.Namespace) -> int:
    payload = event_payload(os.environ)
    path = checkout_path(payload)
    if path is None:
        raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")
        print(
            f"herdr-jj reap: no checkout path in event payload: {raw[:400]}",
            file=sys.stderr,
        )
        return 0
    primary = repo_root_from_payload(payload)
    if primary is None and path.is_dir():
        try:
            primary = primary_root(path)
        except JjError:
            primary = None
    # A plain git repo or a vanished repo has no jj state to reap.
    if primary is None or not primary.is_dir() or not (primary / ".jj").is_dir():
        return 0
    name = workspace_name_for(path, primary)
    if name is None:
        print(
            f"herdr-jj reap: cannot resolve a jj workspace name for {path}",
            file=sys.stderr,
        )
        return 0
    branch = (payload.get("worktree") or {}).get("branch")
    return reap_checkout(
        path, primary, name, branch if isinstance(branch, str) else None
    )


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "reap", help="forget jj state for a herdr-removed worktree"
    )
    parser.set_defaults(run=run)
