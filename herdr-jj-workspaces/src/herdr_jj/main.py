"""herdr-jj: herdr integration for jj workspaces."""

import argparse
import os
import sys

from . import (
    actions,
    adopt,
    close,
    hunk_pane,
    nest,
    new,
    open,
    picker,
    reap,
    remove,
    reporter,
    wizard,
)


def run_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import run

    return run(args)


def main() -> int:
    parser = argparse.ArgumentParser(prog="herdr-jj", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    open.add_parser(subparsers)
    new.add_parser(subparsers)
    close.add_parser(subparsers)
    actions.add_parser(subparsers)
    wizard.add_parser(subparsers)
    remove.add_parser(subparsers)
    picker.add_parser(subparsers)
    reap.add_parser(subparsers)
    reporter.add_parser(subparsers)
    nest.add_parser(subparsers)
    adopt.add_parser(subparsers)
    hunk_pane.add_parser(subparsers)
    dashboard = subparsers.add_parser(
        "dashboard", help="cross-repository workspace dashboard"
    )
    dashboard.add_argument("--json", action="store_true")

    dashboard.set_defaults(run=run_dashboard)
    args = parser.parse_args()
    if args.command == "hunk-pane":
        return hunk_pane.run(args)
    rc = args.run(args)
    # Popup terminals close the moment the command exits; hold failed ones
    # open so the error is readable. Only pane entrypoints get a TTY.
    if rc and os.environ.get("HERDR_PLUGIN_ENTRYPOINT_ID") and sys.stdin.isatty():
        try:
            input("press enter to close")
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
