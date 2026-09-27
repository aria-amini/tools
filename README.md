# herdr-jj dashboard

Open the dashboard with `prefix+u` or `herdr-jj dashboard`.
Use `prefix+shift+g` for the existing jj-waltz create action.

## Model

Each row is one jj workspace with all of its agents, identified by checkout path.
The primary checkout displays as `default` under its repository heading.
In activity order, the primary checkout uses the repository name; other workspaces use `repo/name`.

The default lens shows only active workspaces: a live session, an agent,
a dirty working copy, commits ahead of trunk, or a conflict.
Press `a` to include parked workspaces; they render dimmed with a hollow dot.
Conflicts always stay visible. The default order stays stable across status changes.

Two view modes share the same rows and actions; `g` toggles and the
preference persists:

- **Grouped by repo** (default) — explicit repository headings, primary checkout
  first, then workspace names in alphabetical order. Selection skips headings.
- **Sorted by activity** — a priority queue across repositories.

The columns show workspace, agent states, bookmark relationship, PR, and stack
diff numbers. A dot marks the checkout the dashboard was launched from.
Initial selection starts there when it is visible.

Task workspaces use an exact workspace-name bookmark match. No bookmark is required.
The default checkout uses the configured trunk bookmark, or the sole local `main` or `master` bookmark.
The bookmark column shows work beyond the bookmark, a bookmark ahead of the
workspace, or divergence with nerd-font arrows; `at tip` is omitted. An empty,
undescribed tip does not count as work beyond the bookmark. A trailing bookmark
is neutral. The dashboard does not fetch or move bookmarks.
The PR column matches a task workspace's bookmark to a pull request through one
`gh pr list` call per repository, cached on disk for two minutes; when `gh` is
unavailable it shows a dash. Trunk rows never show a PR.

Discovery uses only live herdr workspaces: registered worktree checkouts first,
then pane working directories. One dashboard row exists per jj checkout; two
herdr workspaces on the same checkout merge into one row. jj workspaces without
a live herdr workspace stay absent until opened; `herdr-jj open` and the picker
reuse the existing herdr workspace for a checkout instead of creating a second
one, and closing a herdr workspace keeps the checkout on disk.

The table refreshes every five seconds with at most eight concurrent repository reads.
Each repository supplies workspace metadata and bookmarks through two batch reads.
When a workspace tip matches trunk, collection skips stack and diff queries.
Refresh uses recorded jj state and does not snapshot files.
Merge and cleanup snapshot the selected checkout before their checks.
Ahead means commits outside the local target bookmark, not unpushed commits.
The count excludes an empty, undescribed working-copy tip.
Stack delta measures the change from the common ancestor to the workspace tip.

## Controls

- `j` / `k`, arrows: select a workspace; skip repository headings.
- `Enter`: focus its live pane, or open its checkout in herdr.
- `/`: show the filter bar and filter all workspaces, including parked ones, by
  repository, workspace, bookmark, agent, or state. Enter opens the result.
- `Escape`: clear the filter, hide the bar, or dismiss a dialog.
- `a`: toggle parked workspaces.
- `g`: toggle grouped by repository / sorted by activity.
- `d`: open the recorded stack diff in Hunk; return to the same selection on exit.
- `e`: inspect agents and select one to focus.
- `?`: show all actions.
- `n`: create a workspace in the selected repository through `jw`.
- `M`: inspect a merge plan; choose merge only or merge plus cleanup.
- `R`: remove a merged checkout and close its idle sessions.
- `r`: refresh.
- `q`: close.

## Merge contract

The default target is the sole local `main` or `master` bookmark.
For another target, set the repository configuration:

```sh
jj config set --repo herdr-jj.trunk-bookmark main
```

Merge requires described commits, a conflict-free stack, and idle or done agents.
The confirmation identifies the repository, target, commit count, and operation type.
The service rechecks the plan after confirmation.
If the target is an ancestor, merge advances its bookmark without a rebase.
Otherwise, merge rebases only an isolated mutable stack.
Immutable commits, shared descendants, and other workspace heads block that rebase.
The service never uses `--ignore-immutable` or changes the immutable revset.

After a rebase, conflicts remain in the source workspace.
The target stays unchanged until the entire stack passes the conflict check.
A failed merge never triggers cleanup.

Cleanup requires no unmerged work and an exact match between jj and jw checkout paths.
`jw remove --keep-bookmark` owns directory removal.
A jw refusal never triggers a raw directory deletion.
Cleanup rechecks terminal identities before it closes herdr sessions.
Mixed-checkout sessions require manual closure.
A cleanup failure leaves the successful merge intact; use `R` to retry cleanup.

A repository lock serializes dashboard actions.
It does not lock external jj commands or filesystem writers.
Revision checks detect changes between stages; bookmark movement also uses `--from`.
The dashboard does not push commits or run project merge hooks.

## Development

```sh
uv sync
uv run pytest
uv run ruff check src tests
uv run herdr-jj dashboard --json
uv tool install --reinstall .
```

The lifecycle tests use disposable repositories with real jj and jw binaries.
Textual pilot tests exercise selection, filters, keyboard actions, confirmation, and partial failure.
