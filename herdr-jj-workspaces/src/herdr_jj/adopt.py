"""Adopt herdr-created Git worktrees into jj and jw.

herdr's worktree create runs plain `git worktree`, which stock jj never
registers as a workspace. The worktree.created hook hands the new checkout
here: jj adopts it as a workspace, jw records it, and configured links apply.
Non-jj repos and non-jj checkouts are skipped quietly.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from . import state
from .lib.jj import JjError, JwError, jj, jw, workspaces

TRUNK_CANDIDATES = ("trunk()", "main@origin", "main", "master@origin", "master")


def event_payload(env: Mapping[str, str]) -> dict:
    raw = env.get("HERDR_PLUGIN_EVENT_JSON")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    # Hook payloads wrap the event body under "data".
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def checkout_path(payload: dict) -> Path | None:
    worktree = payload.get("worktree") or {}
    workspace = payload.get("workspace") or {}
    provenance = workspace.get("worktree") or {}
    for value in (
        worktree.get("checkout_path"),
        worktree.get("path"),
        provenance.get("checkout_path"),
        workspace.get("cwd"),
    ):
        if value:
            return Path(value)
    return None


def branch(payload: dict) -> str | None:
    worktree = payload.get("worktree") or {}
    value = worktree.get("branch")
    return value if isinstance(value, str) and value else None


def git_worktree(path: Path) -> tuple[Path, str] | None:
    """Primary checkout and admin name of the git worktree at path.

    A linked checkout's `.git` is a file: `gitdir: <primary>/.git/worktrees/<name>`.
    """
    ptr = path / ".git"
    if not ptr.is_file():
        return None
    try:
        line = ptr.read_text().strip()
    except OSError:
        return None
    _, separator, gitdir = line.partition("gitdir:")
    if not separator:
        return None
    gitdir_path = Path(gitdir.strip())
    if gitdir_path.parent.name != "worktrees":
        return None
    return gitdir_path.parent.parent.parent, gitdir_path.name


def base_revset(candidate: str | None, primary: Path) -> str:
    """Branch from the event when it resolves; else the trunk ladder."""
    for rev in (candidate, *TRUNK_CANDIDATES):
        if not rev:
            continue
        try:
            jj("log", "--no-graph", "-r", rev, "-T", "", cwd=primary)
        except JjError:
            continue
        return rev
    return "root()"


def adopt(path: Path, event_branch: str | None) -> str | None:
    """Adopt the checkout at path. Returns the workspace name, None if skipped."""
    found = git_worktree(path)
    if found is None:
        return None
    primary, name = found
    if not (primary / ".jj").is_dir():
        return None
    if not any(item.root == path for item in workspaces(primary)):
        jj("-R", str(primary), "git", "worktree", "adopt", name)
    base = base_revset(event_branch, primary)
    cmd = ["adopt", name, "--base", base]
    if event_branch and base == event_branch:
        cmd.extend(["--bookmark", event_branch])
    jw(*cmd, cwd=path)
    jw("links", "apply", cwd=path)
    return name


def run(args: argparse.Namespace) -> int:
    payload = event_payload(os.environ)
    path = checkout_path(payload)
    if path is None:
        raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")
        print(
            f"herdr-jj adopt: no checkout path in event payload: {raw[:400]}",
            file=sys.stderr,
        )
        return 0
    if not path.is_dir():
        return 0
    try:
        name = adopt(path, branch(payload))
    except (JjError, JwError, OSError) as error:
        print(f"herdr-jj adopt: {path}: {error}", file=sys.stderr)
        return 1
    if name is None:
        return 0
    found = git_worktree(path)
    if found is not None:
        primary, _ = found
        entries = state.read_ledger()
        entries[str(path.resolve())] = {"name": name, "repo": str(primary)}
        state.write_ledger(entries)
    print(f"adopted {name} at {path}")
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "adopt", help="adopt a herdr git worktree into jj and jw"
    )
    parser.set_defaults(run=run)
