"""Cross-repository workspace dashboard."""

import argparse
import asyncio
import json
import os
import posixpath
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import ClassVar

from rich.syntax import Syntax
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from . import finish
from .dashboard_data import (
    PR_ICON,
    DashboardError,
    Row,
    Snapshot,
    checkout,
    collect,
    command,
    live_paths,
    live_state,
    quote,
    single,
    trunk_bookmark,
)
from .finish import DiffFile, DiffStat
from .state import resolve_context


class Dialog(ModalScreen[str]):
    BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    Dialog { align: center middle; background: $background 70%; }
    Dialog > Vertical { width: 85%; height: 85%; border: round $accent; padding: 1 2; background: $surface; }
    Dialog VerticalScroll { height: 1fr; }
    Dialog Horizontal { height: 3; align-horizontal: right; }
    Dialog Button { margin-left: 1; }
    """

    def __init__(
        self, title: str, body: str, choices: tuple[tuple[str, str], ...] = ()
    ):
        super().__init__()
        self.heading, self.body, self.choices = title, body, choices

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.heading, style="bold cyan"))
            with VerticalScroll():
                yield Static(Text(self.body))
            with Horizontal():
                yield Button("Close" if not self.choices else "Cancel", id="cancel")
                for key, label in self.choices:
                    yield Button(label, id=key, variant="primary")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class DiffViewer(ModalScreen[None]):
    """Near-fullscreen stack diff with hunk navigation."""

    BINDINGS: ClassVar = [
        Binding("escape,q", "close_viewer", "Close"),
        Binding("n", "next_hunk", "Next hunk"),
        Binding("p", "prev_hunk", "Prev hunk"),
        Binding("j,down", "scroll_down", show=False),
        Binding("k,up", "scroll_up", show=False),
    ]
    DEFAULT_CSS = """
    DiffViewer { align: center middle; background: $background 80%; }
    DiffViewer > Vertical { width: 96%; height: 96%; border: round $accent; background: $surface; }
    DiffViewer #diff-title { height: 1; padding: 0 1; color: $text-muted; }
    DiffViewer #diff-body { height: 1fr; background: $surface; }
    DiffViewer #diff-hint { height: 1; padding: 0 1; color: $text-disabled; }
    """

    def __init__(self, title: str, diff_text: str):
        super().__init__()
        self.heading = title
        self.lines = diff_text.splitlines()
        self.hunk_lines = [
            index for index, line in enumerate(self.lines) if line.startswith("@@")
        ]
        self.hunk_index = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f" {self.heading} · {len(self.hunk_lines)} hunks",
                id="diff-title",
                markup=False,
            )
            with VerticalScroll(id="diff-body"):
                yield Static(
                    Syntax(
                        "\n".join(self.lines) or "No changes",
                        "diff",
                        theme="ansi_dark",
                        word_wrap=False,
                        line_numbers=True,
                    ),
                    markup=False,
                )
            yield Static(
                " n next hunk · p prev hunk · j/k scroll · q close", id="diff-hint"
            )

    def _scroll_to_hunk(self, index: int) -> None:
        if not self.hunk_lines:
            return
        self.hunk_index = max(0, min(index, len(self.hunk_lines) - 1))
        self.query_one("#diff-body", VerticalScroll).scroll_to(
            y=max(0, self.hunk_lines[self.hunk_index] - 1), animate=False
        )

    def action_next_hunk(self) -> None:
        self._scroll_to_hunk(self.hunk_index + 1)

    def action_prev_hunk(self) -> None:
        self._scroll_to_hunk(self.hunk_index - 1)

    def action_scroll_down(self) -> None:
        self.query_one("#diff-body", VerticalScroll).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#diff-body", VerticalScroll).scroll_up()

    def action_close_viewer(self) -> None:
        self.dismiss(None)


def _stat_sections(files: list[DiffFile]) -> list[tuple[str | None, list[DiffFile]]]:
    groups: dict[str, list[DiffFile]] = {}
    for file in files:
        groups.setdefault(posixpath.dirname(file.path), []).append(file)
    sections = [(d, fs) for d, fs in groups.items()]
    sections.sort(key=lambda s: -sum(f.lines for f in s[1]))
    return [(d + "/" if d else None, fs) for d, fs in sections]


def _scaled_bar(bar: str, width: int) -> tuple[str, str]:
    plus, minus = bar.count("+"), bar.count("-")
    total = plus + minus
    if total == 0 or total <= width:
        return "+" * plus, "-" * minus
    if not minus:
        return "+" * width, ""
    if not plus:
        return "", "-" * width
    minus_w = min(width - 1, max(1, round(minus * width / total)))
    return "+" * (width - minus_w), "-" * minus_w


def _stat_row(file: DiffFile, shown: str, path_w: int, bar_w: int) -> Text:
    shown = shown if len(shown) <= path_w else "…" + shown[-(path_w - 1) :]
    line = Text()
    line.append(shown.ljust(path_w))
    line.append(" │ ", style="dim")
    if file.bar.startswith("Bin"):
        line.append(file.bar, style="dim")
        return line
    line.append(f"{file.lines:>4} ", style="cyan")
    plus, minus = _scaled_bar(file.bar, bar_w)
    line.append(plus, style="green")
    line.append(minus, style="red")
    return line


class Prompt(ModalScreen[str | None]):
    BAR_WIDTH: ClassVar = 14
    AUTO_FOCUS = "#value"

    BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    Prompt { align: center middle; background: $background 70%; }
    Prompt > Vertical { width: 80%; height: auto; max-height: 80%; border: round $accent; padding: 1 2; background: $surface; }
    Prompt #summary { color: $text-muted; margin-bottom: 1; }
    Prompt #stat { height: auto; max-height: 12; overflow-y: auto; margin-bottom: 1; }
    Prompt #stat-total { text-style: bold; margin-bottom: 1; }
    Prompt Input { margin: 1 0; }
    Prompt #hint { color: $text-disabled; }
    """

    def __init__(self, title: str, body: str | DiffStat = "", value: str = ""):
        super().__init__()
        self.heading = title
        self.body, self.initial = body, value

    def _path_width(self, paths: list[str]) -> int:
        usable = max(48, int(self.app.console.width * 0.8) - 8)
        longest = max((len(p) for p in paths), default=0)
        return max(12, min(longest, usable - 24))

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.heading, style="bold cyan"))
            if isinstance(self.body, DiffStat) and self.body.files:
                sections = _stat_sections(self.body.files)
                shown_paths = [
                    f.path[len(header) :] if header else f.path
                    for header, files in sections
                    for f in files
                ]
                path_w = self._path_width(shown_paths)
                with VerticalScroll(id="stat"):
                    shown = iter(shown_paths)
                    for header, files in sections:
                        if header:
                            yield Static(Text(header, style="dim"), classes="stat-dir")
                        for file in files:
                            yield Static(
                                _stat_row(file, next(shown), path_w, self.BAR_WIDTH)
                            )
                total = (
                    f"{len(self.body.files)} files changed, "
                    f"{self.body.total_adds} insertions(+), "
                    f"{self.body.total_dels} deletions(-)"
                )
                yield Static(Text(total, style="bold"), id="stat-total")
            elif isinstance(self.body, DiffStat):
                yield Static(
                    Text("No changes in the working copy", style="dim"),
                    id="stat-total",
                )
            else:
                yield Static(Text(self.body), id="summary")
            yield Input(value=self.initial, placeholder="Message", id="value")
            yield Static("enter commit · esc cancel", id="hint")

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NewWorkspace(ModalScreen[tuple[str, str] | None]):
    BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    NewWorkspace { align: center middle; background: $background 70%; }
    NewWorkspace > Vertical { width: 75%; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    NewWorkspace Input { margin: 1 0; }
    """

    def __init__(self, repo: Path, base: str):
        super().__init__()
        self.repo, self.base = repo, base

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(f"New workspace · {self.repo}"))
            yield Input(placeholder="Workspace name", id="name")
            yield Input(value=self.base, placeholder="Base revset", id="base")
            yield Button("Create", id="create", variant="primary")

    @on(Button.Pressed)
    def submit(self) -> None:
        self.dismiss(
            (
                self.query_one("#name", Input).value.strip(),
                self.query_one("#base", Input).value.strip(),
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


class Dashboard(App):
    TITLE = "jj workspaces"
    CSS = """
    Screen { background: $background; }
    #search {
        height: 1; margin: 0 1; border: none; background: transparent;
        padding: 0;
    }
    #search:focus { border: none; background: transparent; }
    .hidden { display: none; }
    #workspaces {
        height: 1fr; margin: 0 1;
        background: transparent;
    }
    # Header is rendered inline by DataTable (no child widget), so this is a
    # component-class style: no borders. A distinct band plus underline
    # separates it from the first row.
    #workspaces .datatable--header {
        color: $text-muted; text-style: bold underline; background: $foreground 12%;
    }
    #workspaces > .datatable--cursor { background: $boost; }
    #workspaces > .datatable--hover { background: transparent; }
    #footer {
        height: 1; padding: 0 2; color: $text-muted;
        background: transparent;
    }
    """
    PR_STYLE: ClassVar = {"OPEN": "green", "MERGED": "dim", "CLOSED": "red"}
    STATE_STYLE: ClassVar = {
        "working": ("cyan", "bold"),
        "blocked": ("yellow", "bold"),
        "done": ("green", ""),
        "idle": ("dim", ""),
        "unknown": ("dim", "italic"),
        "shell": ("blue", ""),
        "closed": ("dim", ""),
    }
    BINDINGS: ClassVar = [
        Binding("enter", "focus_row", "Open"),
        Binding("d", "diff", "Diff"),
        Binding("c", "commit", "Commit"),
        Binding("p", "push", "Push"),
        Binding("P", "pull_request", "PR"),
        Binding("e", "agents", "Agents"),
        Binding("question_mark", "help", "Actions"),
        Binding("n", "new", "New"),
        Binding("M", "merge", "Merge"),
        Binding("R", "cleanup", "Cleanup"),
        Binding("slash", "search", "Filter"),
        Binding("a", "toggle_all", "All"),
        Binding("g", "toggle_group", "Group"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Close"),
        Binding("j,down", "down", show=False),
        Binding("k,up", "up", show=False),
        Binding("escape", "unfilter", show=False),
    ]

    def __init__(self, launch: Path | None = None, loader=collect, show_all=False):
        super().__init__()
        self.launch, self.loader = launch, loader
        self.snapshot = Snapshot()
        self.displayed_rows: list[Row] = []
        self.rows_by_key: dict[str, Row] = {}
        self.table_rows_by_key: dict[str, int] = {}
        self.refreshing = False
        self.busy = False
        self.show_all = show_all
        self.grouped = True
        self.header_indices: set[int] = set()
        self.last_table_index = 0

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter…", id="search", classes="hidden")
        yield DataTable(id="workspaces", cursor_type="row", zebra_stripes=False)
        yield Static("", id="footer", markup=True)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "workspace", "agents", "bookmark", "pr", "stack diff")
        table.focus()
        self._load_view_preference()
        self.action_refresh()
        # The recurring refresh worker races test workers' completion waits.
        if "pytest" not in sys.modules:
            self.set_interval(5, self.action_refresh)
        self.render_chrome()

    def render_chrome(self) -> None:
        keys = [
            ("enter", "open"),
            ("d", "diff"),
            ("c", "commit"),
            ("p", "push"),
            ("P", "pr"),
            ("M", "merge"),
            ("/", "filter"),
            ("?", "all"),
        ]
        spans = []
        for index, (key, label) in enumerate(keys):
            if index:
                spans.append("[dim] │ [/]")
            spans.append(f"[bold]{key}[/] [dim]{label}[/]")
        self.query_one("#footer", Static).update("  " + "".join(spans))

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.render_rows()

    def action_toggle_group(self) -> None:
        self.grouped = not self.grouped
        self._save_view_preference()
        self.render_rows()

    def _preference_path(self):
        import os

        from .state import state_dir

        return state_dir(os.environ) / "dashboard-view.json"

    def _load_view_preference(self) -> None:
        import json

        try:
            self.grouped = json.loads(self._preference_path().read_text())["grouped"]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_view_preference(self) -> None:
        import json

        try:
            path = self._preference_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"grouped": self.grouped}))
        except OSError:
            pass

    @property
    def selected(self) -> Row | None:
        table = self.query_one(DataTable)
        if not table.row_count or not self.displayed_rows:
            return None
        row_key, _ = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        return self.rows_by_key.get(row_key.value or "")

    def status(self, text: str) -> None:
        # Transient: the next refresh re-renders the keybind footer over it.
        self.query_one("#footer", Static).update(Text(text))

    @work(group="refresh")
    async def action_refresh(self) -> None:
        if self.refreshing or self.busy or len(self.screen_stack) > 1:
            return
        self.refreshing = True
        try:
            self.snapshot = await asyncio.to_thread(self.loader, self.launch)
            self.render_rows()
            errors = " · ".join(self.snapshot.errors)
            if errors:
                self.status(errors)
        except (DashboardError, OSError, ValueError) as error:
            self.status(f"Refresh failed: {error}")
        finally:
            self.refreshing = False

    def layout(self) -> list[tuple[Row, int, str]]:
        """Rows with indent depth and display name for the current view mode."""
        if not self.grouped:
            return [(row, 0, row.display) for row in self.displayed_rows]

        def group_key(row: Row) -> str:
            return str(row.repo or row.root or row.name)

        def member_key(row: Row) -> tuple[int, ...]:
            if row.is_primary:
                return (0,)
            return (1,)

        ordered = sorted(
            self.displayed_rows,
            key=lambda row: (group_key(row), *member_key(row), row.name.casefold()),
        )
        entries: list[tuple[Row, int, str]] = []
        for row in ordered:
            depth = 1
            if row.is_primary:
                name = "default"
            else:
                name = row.name
            entries.append((row, depth, name))
        return entries

    def render_rows(self) -> None:
        old = self.selected.key if self.selected else None
        query = self.query_one(Input).value.casefold()
        candidates = [
            r for r in self.snapshot.rows if self.show_all or r.active or query
        ]
        self.displayed_rows = [
            r
            for r in candidates
            if query
            in f"{r.repo} {r.name} {r.display} {r.agent_summary} {r.bookmark} {r.flags} {' '.join(a.name for a in r.agents)}".casefold()
        ]
        self.displayed_rows.sort(key=lambda row: row.sort_key)
        self.rows_by_key = {row.key: row for row in self.displayed_rows}
        table = self.query_one(DataTable)
        scroll = table.scroll_offset
        table.clear()
        self.table_rows_by_key: dict[str, int] = {}
        entries = self.layout()
        self.displayed_rows = [row for row, _, _ in entries]
        self.header_indices = set()
        previous = None
        for row, depth, name in entries:
            group = str(row.repo or row.root or row.name)
            if self.grouped and group != previous:
                self.header_indices.add(table.row_count)
                heading = (
                    row.repo.name
                    if row.repo
                    else (row.root.name if row.root else row.name)
                )
                table.add_row(
                    "",
                    Text(heading, style="bold dim"),
                    "",
                    "",
                    "",
                    "",
                )
            previous = group
            self.table_rows_by_key[row.key] = table.row_count
            table.add_row(*self.workspace_cells(row, depth, name), key=row.key)
        initial = next((r.key for r in self.displayed_rows if r.current), "")
        first = next(iter(self.table_rows_by_key.values()), 0)
        table.move_cursor(row=self.table_rows_by_key.get(old or initial, first))
        if old in self.table_rows_by_key:
            self.call_after_refresh(
                table.scroll_to, x=scroll.x, y=scroll.y, animate=False
            )
        self.render_chrome()
        self.show_detail()

    def workspace_cells(
        self, row: Row, depth: int = 0, name: str | None = None
    ) -> tuple[Text, ...]:
        state_style, _ = self.STATE_STYLE.get(row.status, ("dim", ""))
        parked = not row.active
        dim = " dim" if parked else ""
        live = bool(row.sessions)
        label = row.display if name is None else name
        label = ("  " * depth) + label
        name_text = Text(
            label,
            style=f"{dim} {'bold' if live and not parked and depth == 0 else ''}".strip(),
        )
        adds, _, dels = row.delta.partition(" ")
        stack = Text.assemble(
            (adds, ("green" if adds not in ("+0", "") else "dim") + dim),
            (" ", ""),
            (dels or "—", ("red" if dels not in ("−0", "") else "dim") + dim),
        )
        if row.delta == "—":
            stack = Text("—", style="dim")
        flags = Text()
        for token in row.flags.split(", "):
            if not token:
                continue
            style = {"conflict": "bold yellow", "missing": "bold red"}.get(token, "")
            flags.append(token, style=style)
            flags.append("  ", style="dim")
        if row.error:
            flags.append("error", style="bold red")
        if flags.plain.strip():
            name_text.append("  ")
            name_text.append_text(flags)
        bookmark = Text("—", style="dim")
        if row.bookmark:
            bookmark = Text(row.bookmark, style="dim")
            if row.bookmark_state not in ("at tip",):
                bookmark.append(f" · {row.bookmark_state}")
        pr = Text("—", style="dim")
        if row.pr:
            pr = Text(
                f"{PR_ICON} {row.pr}", style=self.PR_STYLE.get(row.pr_state, "dim")
            )
        return (
            Text("•" if row.current else "", style="cyan"),
            name_text,
            Text(row.agent_summary, style=state_style),
            bookmark,
            pr,
            stack,
        )

    @on(Input.Changed, "#search")
    def filter_changed(self) -> None:
        self.render_rows()

    @on(DataTable.RowHighlighted)
    def show_detail(self) -> None:
        # Repository headings are not selectable; step the cursor past them.
        table = self.query_one(DataTable)
        if table.cursor_row in self.header_indices:
            direction = -1 if table.cursor_row < self.last_table_index else 1
            target = table.cursor_row + direction
            if target < 0:
                target = table.cursor_row + 1
            table.move_cursor(row=target)
            return
        self.last_table_index = table.cursor_row

    def action_search(self) -> None:
        box = self.query_one("#search")
        box.remove_class("hidden")
        box.focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {
            "focus_row",
            "diff",
            "commit",
            "push",
            "pull_request",
            "agents",
            "help",
            "merge",
            "cleanup",
            "new",
            "search",
            "down",
            "up",
            "unfilter",
        }:
            return not self.busy and len(self.screen_stack) == 1
        return True

    @on(DataTable.RowSelected)
    def open_selected(self) -> None:
        self.action_focus_row()

    @on(Input.Submitted, "#search")
    def finish_search(self) -> None:
        self.query_one(DataTable).focus()
        self.action_focus_row()

    def action_unfilter(self) -> None:
        box = self.query_one("#search")
        box.value = ""
        box.add_class("hidden")
        self.query_one(DataTable).focus()
        self.render_rows()

    def _step(self, offset: int) -> None:
        current = self.selected
        if current is None:
            return
        index = self.displayed_rows.index(current) + offset
        if 0 <= index < len(self.displayed_rows):
            self.query_one(DataTable).move_cursor(
                row=self.table_rows_by_key[self.displayed_rows[index].key]
            )

    def action_down(self) -> None:
        self._step(1)

    def action_up(self) -> None:
        self._step(-1)

    def action_quit(self) -> None:
        if self.busy:
            self.status("Wait for the current action to finish")
        else:
            self.exit()

    async def perform(self, function, *args):
        if self.busy:
            raise DashboardError("Another action is active")
        self.busy = True
        try:
            return await asyncio.to_thread(function, *args)
        finally:
            self.busy = False

    def report_error(self, error: Exception) -> None:
        self.push_screen(Dialog("Action stopped", str(error)))

    @work(group="action")
    async def action_focus_row(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one(DataTable).focus()
            return
        row = self.selected
        if not row or row.root is None or self.busy:
            return

        def focus():
            from .lib.herdr import herdr_bin

            sessions, panes = live_state()
            paths = live_paths(sessions, panes)
            found = next((key for key, path in paths.items() if path == row.root), None)
            if found is None:
                found = next(
                    (
                        p["workspace_id"]
                        for p in panes
                        if checkout(Path(p.get("cwd", "/"))) == row.root
                    ),
                    None,
                )
            if found is None:
                if row.repo:
                    command(
                        "herdr-jj",
                        "open",
                        str(row.root),
                        "--project-path",
                        str(row.repo),
                        timeout=120,
                    )
                    return
                raise DashboardError("Session is no longer available")
            session = next(
                (s for s in sessions if s.get("workspace_id") == found), None
            )
            if (
                session is not None
                and not session.get("worktree")
                and row.repo is not None
            ):
                # Created outside this plugin (e.g. jj-waltz); attach nesting.
                command(
                    herdr_bin(),
                    "worktree",
                    "open",
                    "--cwd",
                    str(row.repo),
                    "--path",
                    str(row.root),
                    "--no-focus",
                )
            command(herdr_bin(), "workspace", "focus", found)

        try:
            await self.perform(focus)
            self.exit()
        except (DashboardError, OSError) as error:
            self.report_error(error)

    @work(group="action")
    async def action_diff(self) -> None:
        row = self.selected
        if not row or not row.repo or self.busy:
            return
        repo = row.repo

        def diff():
            trunk = single(repo, f"bookmarks(exact:{quote(trunk_bookmark(repo))})")
            head = single(repo, f"{quote(row.name)}@")
            base = single(repo, f"fork_point({trunk} | {head})")
            return base, head

        try:
            base, head = await self.perform(diff)
        except (DashboardError, OSError) as error:
            self.report_error(error)
            return
        self.busy = True
        try:
            with self.suspend():
                result = await asyncio.to_thread(
                    subprocess.run, ["hunk", "diff", base, head], cwd=repo, check=False
                )
            if result.returncode:
                self.status(f"diff exited with status {result.returncode}")
        finally:
            self.busy = False

    @work(group="action")
    async def action_agents(self) -> None:
        row = self.selected
        if row:
            answer = await self.push_screen_wait(
                Dialog(
                    f"Agents · {row.name}",
                    "\n".join(
                        f"{a.name} · {a.status} · pane {a.pane_id} · session {a.session_id}"
                        for a in row.agents
                    )
                    or "No agents in this workspace",
                    tuple(
                        (f"agent-{i}", f"{a.name} · {a.status}")
                        for i, a in enumerate(row.agents)
                    ),
                )
            )
            if answer.startswith("agent-"):
                agent = row.agents[int(answer.removeprefix("agent-"))]

                def focus_agent():
                    from .lib.herdr import herdr_bin

                    _, panes = live_state()
                    pane = next(
                        (p for p in panes if p.get("terminal_id") == agent.terminal_id),
                        None,
                    )
                    if not agent.terminal_id or pane is None:
                        raise DashboardError("Agent is no longer available")
                    command(herdr_bin(), "pane", "focus", pane["pane_id"])

                try:
                    await self.perform(focus_agent)
                    self.exit()
                except (DashboardError, OSError) as error:
                    self.report_error(error)

    def action_help(self) -> None:
        self.push_screen(
            Dialog(
                "Workspace actions",
                "Enter  Open workspace\nj/k or arrows  Select workspace\n"
                "Esc  Clear filter or close dialog\n"
                "/  Filter all workspaces\nd  Open stack diff\n"
                "c  Commit the working copy\np  Push the bookmark\n"
                "P  Create a pull request\ne  Select agent\n"
                "n  New workspace\na  Include parked workspaces\n"
                "g  Toggle activity order\nM  Merge (squash or rebase)\n"
                "R  Cleanup\nr  Refresh\nq  Close",
            )
        )

    @work(group="action")
    async def action_commit(self) -> None:
        row = self.selected
        if not row or row.root is None or self.busy:
            return
        root = row.root
        try:
            summary, description = await self.perform(finish.working_copy_summary, root)
            message = await self.push_screen_wait(
                Prompt(
                    f"Commit · {row.display}",
                    body=summary,
                    value=description,
                )
            )
            if not message:
                return
            self.status(await self.perform(finish.commit, root, message))
            self.action_refresh()
        except (DashboardError, OSError) as error:
            self.report_error(error)

    def _task_bookmark(self, row: Row) -> str:
        if not row.bookmark:
            raise DashboardError("This workspace has no bookmark to publish")
        if row.is_primary:
            raise DashboardError("Create a task workspace to publish its own bookmark")
        return row.bookmark

    @work(group="action")
    async def action_push(self) -> None:
        row = self.selected
        if not row or row.root is None or self.busy:
            return
        try:
            bookmark = self._task_bookmark(row)
            answer = await self.push_screen_wait(
                Dialog(
                    "Push workspace",
                    f"Push {bookmark} ({row.bookmark_state}) to the remote?\n\n"
                    "Remote bookmarks move to the bookmark, not the workspace tip.",
                    (("push", "Push"),),
                )
            )
            if answer != "push":
                return
            self.status(await self.perform(finish.push_bookmark, row.root, bookmark))
            self.action_refresh()
        except (DashboardError, OSError) as error:
            self.report_error(error)

    @work(group="action")
    async def action_pull_request(self) -> None:
        row = self.selected
        if not row or row.root is None or row.repo is None or self.busy:
            return
        try:
            bookmark = self._task_bookmark(row)
            base = trunk_bookmark(row.repo)
            answer = await self.push_screen_wait(
                Dialog(
                    "Create pull request",
                    f"Push {bookmark} and open a PR against {base}?\n\n"
                    "The title and body come from the first commit.",
                    (("create", "Create"),),
                )
            )
            if answer != "create":
                return
            url = await self.perform(finish.create_pr, row.root, bookmark, base)
            self.push_screen(Dialog("Pull request", url))
            self.action_refresh()
        except (DashboardError, OSError) as error:
            self.report_error(error)

    @work(group="action")
    async def action_merge(self) -> None:
        row = self.selected
        if not row or not row.repo or row.root is None or self.busy:
            return
        try:
            plan = await self.perform(finish.plan_merge, row.root)
            answer = await self.push_screen_wait(
                Dialog(
                    "Merge workspace",
                    (
                        f"{row.root}\n\n"
                        f"{len(plan.commits)} commits · {plan.trunk[:12]} → {plan.tip[:12]}\n\n"
                        "Squash folds the stack into one new commit on the target.\n"
                        "Rebase keeps the commits and advances the target.\n"
                        "A cleanup failure leaves the merge intact."
                    ),
                    (
                        ("squash", "Squash"),
                        ("rebase", "Rebase"),
                        ("squash-finish", "Squash + cleanup"),
                        ("rebase-finish", "Rebase + cleanup"),
                    ),
                )
            )
            if answer == "cancel":
                return
            if answer.startswith("squash"):
                message = await self.perform(finish.squash_merge, plan)
            else:
                message = await self.perform(finish.merge, plan)
            if answer.endswith("finish"):
                try:
                    message += "\n" + await self.perform(finish.cleanup, row.root)
                except (DashboardError, OSError) as error:
                    message += f"\nCleanup pending: {error}"
            self.push_screen(Dialog("Merge result", message))
        except (DashboardError, OSError) as error:
            self.report_error(error)

    @work(group="action")
    async def action_cleanup(self) -> None:
        row = self.selected
        if not row or not row.repo or row.root is None or self.busy:
            return
        answer = await self.push_screen_wait(
            Dialog(
                "Cleanup workspace",
                f"Remove {row.root} and close its idle sessions?\n\n"
                "Cleanup requires all work to be merged. Bookmarks remain.",
                (("remove", "Cleanup"),),
            )
        )
        if answer == "remove":
            try:
                message = await self.perform(finish.cleanup, row.root)
                self.push_screen(Dialog("Cleanup result", message))
            except (DashboardError, OSError) as error:
                self.report_error(error)

    @work(group="action")
    async def action_new(self) -> None:
        row = self.selected
        if not row or not row.repo or self.busy:
            return
        repo = row.repo
        try:
            base = await self.perform(trunk_bookmark, row.repo)
            answer = await self.push_screen_wait(NewWorkspace(row.repo, base))
            if answer is None:
                return
            name, rev = answer
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
                raise DashboardError(
                    "Use a workspace name that starts with a letter or digit"
                )

            def create():
                target = single(repo, rev)
                command("jw", "add", name, "--at", target, cwd=repo, timeout=120)

            await self.perform(create)
            self.status(f"Created {name}; select it and press Enter to open")
            self.action_refresh()
        except (DashboardError, OSError) as error:
            self.report_error(error)


def run(args: argparse.Namespace) -> int:
    launch = checkout(Path(resolve_context(os.environ)["cwd"]).resolve())
    # Cleanup can remove the launch checkout; subprocesses need a surviving cwd.
    os.chdir(Path.home())
    if args.json:
        print(json.dumps(asdict(collect(launch)), default=str, indent=2))
    else:
        Dashboard(launch).run()
    return 0
