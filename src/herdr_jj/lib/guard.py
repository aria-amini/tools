"""Shared state with the aamini.cd-guard herdr plugin / zsh hook.

The `roots` file (<workspace_id>\t<root> per line) in the plugin config dir is
the registry of managed herdr workspaces: the zsh hook only enforces the cd
guard in workspaces listed here. `herdr-jj open` arms workspaces it opens;
`herdr-jj close` disarms them.
"""

import os
from pathlib import Path

PLUGIN_ID = "aamini.cd-guard"


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "herdr" / "plugins" / "config" / PLUGIN_ID


def _read(path: Path) -> list[str]:
    try:
        return path.read_text().splitlines()
    except FileNotFoundError:
        return []


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines))


def arm(workspace_id: str, root: Path, live_ids: set[str] | None = None) -> None:
    """Record workspace_id as guarded with the given root.

    Entries for ids that are no longer live are pruned when live_ids is given
    (herdr workspace ids are session-scoped).
    """
    path = config_dir() / "roots"
    lines = []
    for line in _read(path):
        entry_id, sep, _ = line.partition("\t")
        if not sep or entry_id == workspace_id:
            continue
        if live_ids is not None and entry_id not in live_ids:
            continue
        lines.append(line)
    lines.append(f"{workspace_id}\t{root}")
    _write(path, lines)


def disarm(workspace_id: str) -> None:
    for name in ("roots", "disabled"):
        path = config_dir() / name
        _write(
            path, [line for line in _read(path) if line.split("\t")[0] != workspace_id]
        )


def prune(live_ids: set[str]) -> None:
    """Drop entries whose herdr workspace ids are no longer live.

    Covers leaks from workspaces closed by hand or from earlier herdr sessions
    (ids are session-scoped).
    """
    for name in ("roots", "disabled"):
        path = config_dir() / name
        _write(path, [line for line in _read(path) if line.split("\t")[0] in live_ids])
