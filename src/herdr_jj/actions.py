"""Keybound actions (no TTY): stash context and open the popup panes."""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from . import state
from .lib.herdr import herdr
from .lib.jj import JjError, primary_root
from .reporter import ensure
from .state import resolve_context

PLUGIN_ID = "aamini.jj"


def _open_pane(pane: str, env: Mapping[str, str]) -> int:
    cwd = Path(resolve_context(env)["cwd"])
    try:
        primary_root(cwd)
    except JjError as error:
        print(f"herdr-jj: {error}", file=sys.stderr)
        return 1
    state.write_context(env, {"cwd": str(cwd)})
    herdr("plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", pane)
    ensure(env)
    return 0


def pick(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    return _open_pane("picker", env)


def menu(env: Mapping[str, str] | None = None) -> int:
    # The menu works outside jj repos by design (it picks the project first),
    # so it skips the in-repo guard _open_pane applies.
    return _open_new_pane("menu", env)


def new(env: Mapping[str, str] | None = None) -> int:
    # Creation shares the menu's reach: the pane picks the project first
    # when the context is not already inside a repo.
    return _open_new_pane("new", env)


def _open_new_pane(pane: str, env: Mapping[str, str] | None) -> int:
    env = os.environ if env is None else env
    cwd = Path(resolve_context(env)["cwd"])
    state.write_context(env, {"cwd": str(cwd)})
    herdr("plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", pane)
    ensure(env)
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("pick", help="open the workspace picker popup").set_defaults(
        run=lambda args: pick()
    )
    subparsers.add_parser(
        "menu", help="open the workspace dashboard popup"
    ).set_defaults(run=lambda args: menu())
    subparsers.add_parser(
        "new-action", help="open the new-workspace popup"
    ).set_defaults(run=lambda args: new())
