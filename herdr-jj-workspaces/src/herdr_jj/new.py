"""Create a jj workspace non-interactively and open it with the default layout.

Thin wrapper so scripts, keybindings, and agents share one creation path:
jw owns the workspace, herdr-jj open owns the layout.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .open import open_workspace


def _jw(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["jw", *args], cwd=project, text=True, capture_output=True, check=False
    )


def new_workspace(name: str, at: str | None, project: Path) -> int:
    add_args = ["add", name] if at is None else ["add", name, "--at", at]
    added = _jw(project, *add_args)
    resolved = _jw(project, "path", name)
    if added.returncode and resolved.returncode:
        print(
            f"herdr-jj new: jw add {name} failed: {added.stderr.strip()}",
            file=sys.stderr,
        )
        return 1
    if added.returncode:
        print(f"herdr-jj: workspace {name} exists; opening", file=sys.stderr)
    resolved_path = resolved.stdout.strip()
    if not resolved_path:
        print(f"herdr-jj new: jw path {name} printed nothing", file=sys.stderr)
        return 1
    return open_workspace(Path(resolved_path), project, workspace_name=name)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "new", help="create a jj workspace and open it with the default layout"
    )
    parser.add_argument("name", help="jj workspace name")
    parser.add_argument(
        "--at",
        default=None,
        help="base revset (default: jw's implicit parents(@) rule)",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="primary workspace that owns .herdr.toml (defaults to cwd)",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    project = args.project.resolve() if args.project else Path.cwd()
    return new_workspace(args.name, args.at, project)
