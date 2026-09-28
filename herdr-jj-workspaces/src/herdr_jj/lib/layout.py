"""Per-repo workspace layout from the repository's .herdr.toml.

[layout]
agent = "opencode"  # left pane command
setup = "fastfetch" # right pane command; "" disables
"""

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import herdr as herdr_module

DEFAULT_AGENT = "opencode"
DEFAULT_SETUP = "fastfetch"


@dataclass(frozen=True)
class Layout:
    agent: str = DEFAULT_AGENT
    setup: str = DEFAULT_SETUP


def load(repo_root: Path) -> Layout:
    config = repo_root / ".herdr.toml"
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Layout()
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        print(f"herdr-jj: ignoring {config}: {error}", file=sys.stderr)
        return Layout()

    section = data.get("layout")
    if not isinstance(section, dict):
        return Layout()

    agent = section.get("agent", DEFAULT_AGENT)
    setup = section.get("setup", DEFAULT_SETUP)
    return Layout(
        agent=agent if isinstance(agent, str) else DEFAULT_AGENT,
        setup=setup if isinstance(setup, str) else DEFAULT_SETUP,
    )


def apply(left_pane: str, layout: Layout) -> str:
    """Split a lone pane right and run agent and setup in the halves."""
    right = herdr_module.herdr(
        "pane", "split", left_pane, "--direction", "right", "--no-focus"
    )["pane"]["pane_id"]
    herdr_module.herdr("pane", "run", left_pane, layout.agent)
    if layout.setup:
        herdr_module.herdr("pane", "run", right, layout.setup)
    return right
