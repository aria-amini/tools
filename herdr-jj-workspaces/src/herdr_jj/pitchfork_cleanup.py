"""Release Pitchfork daemons and proxy slugs after Herdr deletes a checkout."""

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from .adopt import checkout_path, event_payload


def load(path: Path) -> dict:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except FileNotFoundError:
        return {}


def belongs_to(directory: str | None, checkout: Path) -> bool:
    if not directory:
        return False
    return Path(directory).expanduser().resolve().is_relative_to(checkout)


def cleanup_checkout(checkout: Path) -> None:
    checkout = checkout.resolve()
    try:
        checkout.stat()
    except FileNotFoundError:
        pass
    else:
        return

    def pitchfork(*args: str) -> None:
        # The event's cwd can be the checkout that Herdr just deleted.
        subprocess.run(["pitchfork", *args], cwd=Path.home(), check=True)

    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    state_dir = Path(os.environ.get("PITCHFORK_STATE_DIR", state_home / "pitchfork"))
    state = load(state_dir / "state.toml")
    for daemon_id, daemon in state.get("daemons", {}).items():
        if daemon_id.startswith("global/") or not belongs_to(
            daemon.get("dir"), checkout
        ):
            continue
        if daemon.get("pid"):
            pitchfork("stop", daemon_id)
        pitchfork("clean", "--prune", "--daemon", daemon_id)

    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config = load(config_home / "pitchfork/config.toml")
    namespaces = config.get("namespaces", {})
    for slug, entry in config.get("slugs", {}).items():
        directory = entry.get("dir") or namespaces.get(entry.get("namespace"), {}).get(
            "dir"
        )
        if belongs_to(directory, checkout):
            pitchfork("proxy", "remove", slug)


def run(args: argparse.Namespace) -> int:
    checkout = checkout_path(event_payload(os.environ))
    if checkout is None or shutil.which("pitchfork") is None:
        return 0
    try:
        cleanup_checkout(checkout)
    except (OSError, subprocess.CalledProcessError, tomllib.TOMLDecodeError) as error:
        print(f"herdr-jj pitchfork-cleanup: {error}", file=sys.stderr)
        return 1
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "pitchfork-cleanup", help="release Pitchfork servers for a removed checkout"
    )
    parser.set_defaults(run=run)
