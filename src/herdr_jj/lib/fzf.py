"""fzf subprocess wrapper shared by wt and the herdr plugin."""

import subprocess


def fzf_select(
    lines: list[str],
    preview: str | None = None,
    binds: dict[str, str] | None = None,
    expect: tuple[str, ...] = (),
) -> tuple[str, str | None]:
    """Show lines in fzf; return ("enter"|key|"esc", selected line or None)."""
    cmd = ["fzf", "--delimiter=\t"]
    if expect:
        cmd.append(f"--expect={','.join(expect)}")
    if preview:
        cmd += ["--preview", preview]
    for key, action in (binds or {}).items():
        cmd += ["--bind", f"{key}:{action}"]
    try:
        result = subprocess.run(
            cmd, input="\n".join(lines), text=True, capture_output=True, check=False
        )
    except OSError:
        return ("esc", None)
    if result.returncode:
        return ("esc", None)
    out = result.stdout.splitlines()
    if not out:
        return ("esc", None)
    if not expect:
        return ("enter", out[0])
    key = out[0] or "enter"
    line = out[1] if len(out) > 1 else None
    return (key, line)
