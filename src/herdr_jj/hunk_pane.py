"""Plugin pane entrypoint: review the requested diff in hunk."""

import os
import sys


def add_parser(subparsers) -> None:
    subparsers.add_parser("hunk-pane", help="internal: hunk pane entrypoint")


def run(args) -> int:
    repo = os.environ.get("HUNK_REPO")
    if repo:
        os.chdir(repo)
    base, head = os.environ.get("HUNK_BASE"), os.environ.get("HUNK_HEAD")
    argv = ["hunk", "diff"]
    if base and head:
        argv += [base, head]
    try:
        os.execvp(argv[0], argv)
    except OSError as error:
        print(f"hunk failed to start: {error}", file=sys.stderr)
        return 1
