"""Open a jj workspace as a laid-out herdr workspace.

Invoked by herdr-jj with the workspace path. If a herdr workspace labeled with
the jj workspace name already exists it is focused; otherwise it is created
with a vertical split: the left pane runs the layout agent and the right pane
runs the layout setup command. Both default to opencode and `mise run setup`
and can be overridden per repository in .herdr.toml (see lib.layout).
"""

import argparse
import sys
from pathlib import Path

from .lib import guard
from .lib.herdr import (
    HerdrError,
    Workspace,
    ensure_open,
    focus_workspace,
    herdr,
    workspace_label,
)
from .lib.layout import load as load_layout


def herdr_label(path: Path) -> str:
    return workspace_label(path.name)


def open_workspace(
    path: Path,
    project_path: Path | None = None,
    workspace_name: str | None = None,
) -> int:
    name = path.name if workspace_name is None else workspace_name
    created, is_new, workspaces = ensure_open(name, path, project_path=project_path)
    workspace_id = created["workspace"]["workspace_id"]
    if not is_new:
        focus_workspace(workspace_id)
        _arm(workspace_id, path, workspaces)
        return 0

    layout = load_layout(project_path or path)
    left = created["root_pane"]["pane_id"]
    right = herdr("pane", "split", left, "--direction", "right", "--no-focus")["pane"][
        "pane_id"
    ]

    herdr("pane", "run", left, layout.agent)
    if layout.setup:
        herdr("pane", "run", right, layout.setup)
    _arm(workspace_id, path, workspaces)
    focus_workspace(workspace_id)
    return 0


def _arm(workspace_id: str, path: Path, workspaces: list[Workspace]) -> None:
    live_ids = {w["workspace_id"] for w in workspaces} | {workspace_id}
    try:
        guard.arm(workspace_id, path, live_ids)
    except OSError as error:
        print(f"herdr-jj open: cd-guard arm failed: {error}", file=sys.stderr)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("open", help="open a jj workspace in herdr")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="jj workspace path to open (defaults to the current directory)",
    )
    parser.add_argument(
        "--project-path",
        type=Path,
        default=None,
        help="primary jj workspace path; its directory name labels the herdr "
        "workspace (defaults to the parent directory of PATH)",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    path = args.path.resolve()
    project_path = args.project_path.resolve() if args.project_path else None
    try:
        return open_workspace(path, project_path)
    except HerdrError as error:
        print(f"herdr-jj open: {error}", file=sys.stderr)
        return 1
