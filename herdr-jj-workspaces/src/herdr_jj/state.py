"""Plugin state dir: context handoff between actions and popup panes."""

import json
import os
from collections.abc import Mapping
from pathlib import Path

PLUGIN_ID = "aamini.jj"


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get("HERDR_PLUGIN_STATE_DIR")
    if override:
        return Path(override)
    xdg = env.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "herdr" / "plugins" / PLUGIN_ID


def context_path(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "picker-context.json"


def read_context(env: Mapping[str, str] | None = None) -> dict | None:
    try:
        return json.loads(context_path(env).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_context(env: Mapping[str, str] | None, ctx: dict) -> None:
    path = context_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ctx))


def _cwd_from(ctx: dict) -> str | None:
    return ctx.get("focused_pane_cwd") or ctx.get("workspace_cwd") or ctx.get("cwd")


def resolve_context(env: Mapping[str, str]) -> dict:
    raw = env.get("HERDR_PLUGIN_CONTEXT_JSON")
    if raw:
        try:
            ctx = json.loads(raw)
        except json.JSONDecodeError:
            ctx = None
        if ctx:
            cwd = _cwd_from(ctx)
            if cwd:
                return {**ctx, "cwd": cwd}
    ctx = read_context(env)
    if ctx is not None:
        cwd = _cwd_from(ctx)
        if cwd:
            return {**ctx, "cwd": cwd}
    return {"cwd": str(Path.cwd())}
