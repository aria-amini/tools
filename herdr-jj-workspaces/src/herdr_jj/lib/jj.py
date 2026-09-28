"""Thin wrapper around the jj CLI, mirroring herdr.py."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


class JjError(RuntimeError):
    pass


class JwError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    name: str
    root: Path


def jj(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["jj", *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise JjError(
            f"jj {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def jw(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["jw", *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise JwError(
            f"jw {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def jw_add(name: str, cwd: Path, at: str | None = "@") -> None:
    """Create a jw-managed workspace; jw owns paths, links, and bookmarks."""
    args = ["add", name]
    if at is not None:
        args.extend(["--at", at])
    jw(*args, cwd=cwd)


def jw_remove(name: str, cwd: Path) -> None:
    jw("remove", name, "--delete-bookmark", cwd=cwd)


def repo_root(cwd: Path) -> Path:
    return Path(jj("root", cwd=cwd).strip())


def primary_root(cwd: Path) -> Path:
    """Root of the primary workspace for the repo containing cwd.

    A secondary workspace's `.jj/repo` is a pointer file to the store dir in
    the primary workspace; the primary's `.jj/repo` is the store itself.
    """
    root = repo_root(cwd)
    repo_ptr = root / ".jj" / "repo"
    if repo_ptr.is_dir():
        return root
    store = (root / ".jj" / repo_ptr.read_text().strip()).resolve()
    return store.parent.parent


def absolutize_repo_pointer(workspace_root: Path) -> None:
    """Rewrite a relative `.jj/repo` pointer as an absolute path.

    Recent jj versions write a relative path in the pointer file; tools that
    read it directly (e.g. ryu) may resolve it against the process cwd
    instead of the `.jj` dir and fail. The pointer must not have a trailing
    newline — jj does not trim it.
    """
    ptr = workspace_root / ".jj" / "repo"
    if not ptr.is_file():
        return
    target = Path(ptr.read_text().strip())
    if target.is_absolute():
        return
    resolved = (ptr.parent / target).resolve()
    ptr.write_text(str(resolved))


def workspaces(cwd: Path) -> list[Workspace]:
    template = 'self.name() ++ "\\t" ++ self.root() ++ "\\n"'
    out = jj("workspace", "list", "-T", template, cwd=cwd)
    result = []
    for line in out.splitlines():
        name, separator, root = line.partition("\t")
        if name and separator and root:
            result.append(Workspace(name=name, root=Path(root)))
    return result


def workspace(cwd: Path, name: str) -> Workspace:
    found = next((item for item in workspaces(cwd) if item.name == name), None)
    if found is None:
        raise JjError(f"'{name}' is not a jj workspace")
    return found


def current_workspace(cwd: Path) -> Workspace:
    root = repo_root(cwd).resolve()
    found = next(
        (item for item in workspaces(cwd) if item.root.resolve() == root), None
    )
    if found is None:
        raise JjError(f"could not identify jj workspace at {root}")
    return found


def workspace_names(cwd: Path) -> list[str]:
    try:
        return [item.name for item in workspaces(cwd)]
    except JjError:
        out = jj("workspace", "list", cwd=cwd)
        return [
            line.split(":", 1)[0].strip() for line in out.splitlines() if line.strip()
        ]


def forget_workspace(name: str, cwd: Path) -> None:
    jj("workspace", "forget", name, cwd=cwd)
    _prune_git_worktrees(name, cwd)


def _prune_git_worktrees(name: str, primary: Path) -> None:
    # wt trashes the workspace directory before forgetting, so the fork's
    # `workspace forget --cleanup` (which runs `git worktree remove`) cannot
    # find it and the registration goes stale, so prune the registration and
    # delete the fork's plumbing branch directly. No-op for non-colocated
    # repos and on stock jj, which never registers worktrees.
    if not (primary / ".git").exists():
        return
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "prune"],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(primary), "branch", "-D", f"jj-worktree-{name}"],
        capture_output=True,
        check=False,
    )


def commit_id(revset: str, cwd: Path) -> str:
    return jj("log", "--no-graph", "-r", revset, "-T", "commit_id", cwd=cwd).strip()


def status_token(cwd: Path, rev: str = "@") -> str:
    """Sidebar token: short change id plus ✓ (empty) or ● (dirty)."""
    template = 'change_id.shortest() ++ " " ++ if(empty, "✓", "●")'
    return jj("log", "--no-graph", "-r", rev, "-T", template, cwd=cwd).strip()
