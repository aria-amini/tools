import hashlib
import json
import os

import pytest

from herdr_jj import reporter as reporter_module
from herdr_jj.lib.herdr import HerdrError
from herdr_jj.lib.jj import JjError
from herdr_jj.state import state_dir


@pytest.fixture
def env(tmp_path):
    return {
        "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "state"),
        "HERDR_SOCKET_PATH": str(tmp_path / "sock"),
    }


def write_lock(env, socket_path, pid):
    path = reporter_module.lock_path(env, socket_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "socket_path": socket_path}))
    return path


class TestLockPath:
    def test_name_is_sha1_of_socket_path(self, env):
        socket_path = env["HERDR_SOCKET_PATH"]
        digest = hashlib.sha1(socket_path.encode()).hexdigest()
        assert (
            reporter_module.lock_path(env, socket_path).name
            == f"reporter-{digest}.lock"
        )

    def test_differs_per_socket(self, env):
        a = reporter_module.lock_path(env, "/tmp/a.sock")
        b = reporter_module.lock_path(env, "/tmp/b.sock")
        assert a != b


class TestEnsure:
    def test_spawns_when_no_lockfile(self, env):
        spawned = []
        rc = reporter_module.ensure(
            env, spawn=lambda argv, log: spawned.append(argv) or 4242
        )

        assert rc == 0
        assert len(spawned) == 1
        lock = json.loads(
            reporter_module.lock_path(env, env["HERDR_SOCKET_PATH"]).read_text()
        )
        assert lock == {"pid": 4242, "socket_path": env["HERDR_SOCKET_PATH"]}

    def test_exits_when_lock_alive_and_same_socket(self, env):
        write_lock(env, env["HERDR_SOCKET_PATH"], os.getpid())
        spawned = []

        rc = reporter_module.ensure(
            env, spawn=lambda argv, log: spawned.append(argv) or 1
        )

        assert rc == 0
        assert spawned == []

    def test_replaces_stale_lock_with_dead_pid(self, env):
        write_lock(env, env["HERDR_SOCKET_PATH"], 999999)
        spawned = []

        rc = reporter_module.ensure(
            env, spawn=lambda argv, log: spawned.append(argv) or 4242
        )

        assert rc == 0
        assert len(spawned) == 1
        lock = json.loads(
            reporter_module.lock_path(env, env["HERDR_SOCKET_PATH"]).read_text()
        )
        assert lock["pid"] == 4242

    def test_replaces_lock_for_different_socket(self, env):
        write_lock(env, "/other/sock", os.getpid())
        spawned = []

        rc = reporter_module.ensure(
            env, spawn=lambda argv, log: spawned.append(argv) or 4242
        )

        assert rc == 0
        assert len(spawned) == 1

    def test_lockfile_written_in_state_dir(self, env):
        reporter_module.ensure(env, spawn=lambda argv, log: 4242)
        assert reporter_module.lock_path(
            env, env["HERDR_SOCKET_PATH"]
        ).parent == state_dir(env)

    def test_spawn_failure_is_not_fatal_and_writes_no_lock(self, env, capsys):
        def broken_spawn(argv, log):
            raise OSError("herdr-jj not on PATH")

        rc = reporter_module.ensure(env, spawn=broken_spawn)

        assert rc == 0
        assert not reporter_module.lock_path(env, env["HERDR_SOCKET_PATH"]).exists()
        assert "spawn" in capsys.readouterr().err.lower()


WORKSPACES = [
    {"workspace_id": "w1", "cwd": "/a"},
    {"workspace_id": "w2", "cwd": "/b"},
]

WORKSPACES_NO_CWD = [{"workspace_id": "w1"}, {"workspace_id": "w2"}]


def make_panes(base):
    for name in ("a", "b"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return [
        {
            "workspace_id": "w1",
            "cwd": str(base / "a"),
            "tab_id": "w1:t1",
            "pane_id": "w1:p1",
        },
        {
            "workspace_id": "w2",
            "cwd": str(base / "b"),
            "tab_id": "w2:t1",
            "pane_id": "w2:p1",
        },
    ]


class TestWorkspaceCwds:
    def test_picks_first_pane_per_workspace(self, tmp_path):
        first, second, other = tmp_path / "1", tmp_path / "2", tmp_path / "3"
        for path in (first, second, other):
            path.mkdir()
        panes = [
            {"workspace_id": "w1", "cwd": str(second), "tab_id": "t1", "pane_id": "p2"},
            {"workspace_id": "w1", "cwd": str(first), "tab_id": "t1", "pane_id": "p1"},
            {"workspace_id": "w2", "cwd": str(other), "tab_id": "t1", "pane_id": "p1"},
        ]

        assert reporter_module.workspace_cwds(lambda *a: {"panes": panes}) == {
            "w1": str(first),
            "w2": str(other),
        }

    def test_skips_panes_without_cwd_or_workspace(self):
        panes = [{"cwd": "/x"}, {"workspace_id": "w1"}]

        assert reporter_module.workspace_cwds(lambda *a: {"panes": panes}) == {}

    def test_skips_deleted_cwd_markers(self, tmp_path):
        panes = [
            {
                "workspace_id": "w1",
                "cwd": f"{tmp_path}/gone (deleted)",
                "tab_id": "t1",
                "pane_id": "p1",
            },
            {
                "workspace_id": "w2",
                "cwd": str(tmp_path),
                "tab_id": "t1",
                "pane_id": "p1",
            },
        ]

        assert reporter_module.workspace_cwds(lambda *a: {"panes": panes}) == {
            "w2": str(tmp_path)
        }

    def test_attach_cwds_fills_missing_only(self):
        workspaces = [{"workspace_id": "w1", "cwd": "/keep"}, {"workspace_id": "w2"}]

        reporter_module.attach_cwds(workspaces, {"w2": "/b", "w3": "/c"})
        assert workspaces == [
            {"workspace_id": "w1", "cwd": "/keep"},
            {"workspace_id": "w2", "cwd": "/b"},
        ]


def token_for(mapping):
    def token_fn(ws):
        value = mapping[ws["cwd"]]
        if isinstance(value, Exception):
            raise value
        return {"jj_status": value}

    return token_fn


class TestComputeReports:
    def test_reports_every_poll_to_refresh_the_ttl_lease(self):
        cache = {}
        token_fn = token_for({"/a": "abc ●", "/b": "def ✓"})

        reports = reporter_module.compute_reports(WORKSPACES, cache, token_fn)
        assert [(r.workspace_id, r.tokens["jj_status"]) for r in reports] == [
            ("w1", "abc ●"),
            ("w2", "def ✓"),
        ]

        again = reporter_module.compute_reports(WORKSPACES, cache, token_fn)
        assert [(r.workspace_id, r.tokens["jj_status"]) for r in again] == [
            ("w1", "abc ●"),
            ("w2", "def ✓"),
        ]

    def test_vanished_tokens_are_cleared(self):
        cache = {}
        token_fn = token_for({"/a": "abc ●", "/b": "def ✓"})
        reporter_module.compute_reports(WORKSPACES, cache, token_fn)

        def shrunk(ws):
            return {}

        reports = reporter_module.compute_reports(
            [WORKSPACES[0]], cache, token_fn=shrunk
        )
        assert len(reports) == 1
        assert reports[0].workspace_id == "w1"
        assert reports[0].cleared == ("jj_status",)
        assert reports[0].tokens == {}

    def test_skips_non_jj_workspaces(self):
        cache = {}
        token_fn = token_for({"/a": "abc ●", "/b": JjError("not a repo")})
        reports = reporter_module.compute_reports(WORKSPACES, cache, token_fn)
        assert [r.workspace_id for r in reports] == ["w1"]

    def test_skips_unreachable_cwds(self):
        cache = {}

        def token_fn(cwd):
            raise FileNotFoundError(cwd)

        reports = reporter_module.compute_reports(WORKSPACES, cache, token_fn)
        assert reports == []


class TestPublish:
    def test_report_metadata_call_shape(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            return {}

        reporter_module.publish(
            fake_herdr,
            [reporter_module.Report("w1", {"jj_status": "abc ●"})],
        )
        assert calls == [
            (
                "workspace",
                "report-metadata",
                "w1",
                "--source",
                "aamini.jj",
                "--token",
                "jj_status=abc ●",
                "--ttl-ms",
                "90000",
            )
        ]

    def test_clears_vanished_tokens(self):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            return {}

        reporter_module.publish(
            fake_herdr,
            [
                reporter_module.Report(
                    "w1", {"added": "+3"}, cleared=("jj_status", "removed")
                )
            ],
        )
        assert calls[0] == (
            "workspace",
            "report-metadata",
            "w1",
            "--source",
            "aamini.jj",
            "--token",
            "added=+3",
            "--clear-token",
            "jj_status",
            "--clear-token",
            "removed",
            "--ttl-ms",
            "90000",
        )


class StopLoop(Exception):
    pass


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeEvents:
    def __init__(self, clock, scripted=(), advance=5.0, stop_at=None):
        self.clock = clock
        self.scripted = list(scripted)
        self.advance = advance
        self.stop_at = stop_at

    def read(self, timeout):
        event = self.scripted.pop(0) if self.scripted else None
        self.clock.t += self.advance
        if self.stop_at is not None and self.clock.t >= self.stop_at:
            raise StopLoop
        return event


def run_loop_env(tmp_path):
    return {"HERDR_SOCKET_PATH": str(tmp_path / "sock")}


class TestRunLoop:
    def test_exits_when_socket_connect_fails_repeatedly(self, tmp_path):
        def failing_connector(path):
            raise OSError("connection refused")

        rc = reporter_module.run_loop(
            run_loop_env(tmp_path), connector=failing_connector, sleep=lambda s: None
        )
        assert rc == 1

    def test_exits_after_30s_of_workspace_list_errors(self, tmp_path):
        clock = FakeClock()
        events = FakeEvents(clock)

        def failing_herdr(*args):
            raise HerdrError("server gone")

        def fake_sleep(seconds):
            clock.t += 10.0

        rc = reporter_module.run_loop(
            run_loop_env(tmp_path),
            connector=lambda path: events,
            clock=clock,
            sleep=fake_sleep,
            herdr_fn=failing_herdr,
        )
        assert rc == 1

    def test_exits_when_socket_dies_mid_loop(self, tmp_path):
        clock = FakeClock()

        class DeadEvents:
            def read(self, timeout):
                raise OSError("socket closed")

        def fake_herdr(*args):
            return {"workspaces": []}

        rc = reporter_module.run_loop(
            run_loop_env(tmp_path),
            connector=lambda path: DeadEvents(),
            clock=clock,
            sleep=lambda s: None,
            herdr_fn=fake_herdr,
        )
        assert rc == 1

    def test_refreshes_focused_workspace_on_event(self, tmp_path):
        clock = FakeClock()
        events = FakeEvents(
            clock,
            scripted=[{"type": "workspace.focused", "workspace_id": "w1"}],
            stop_at=5.0,
        )
        calls = []
        panes = make_panes(tmp_path)

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": WORKSPACES_NO_CWD}
            if args[:2] == ("pane", "list"):
                return {"panes": panes}
            return {}

        with pytest.raises(StopLoop):
            reporter_module.run_loop(
                run_loop_env(tmp_path),
                connector=lambda path: events,
                clock=clock,
                sleep=lambda s: None,
                herdr_fn=fake_herdr,
                token_fn=token_for(
                    {
                        str(tmp_path / "a"): "abc ●",
                        str(tmp_path / "b"): JjError("no"),
                    }
                ),
            )

        reports = [c for c in calls if c[:2] == ("workspace", "report-metadata")]
        assert reports
        assert all(c[2] == "w1" for c in reports)
        assert ("--token", "jj_status=abc ●")[0] in reports[0]
        assert "jj_status=abc ●" in reports[0]

    def test_polls_focused_every_5s_others_every_30s(self, tmp_path):
        clock = FakeClock()
        events = FakeEvents(
            clock,
            scripted=[{"type": "workspace.focused", "workspace_id": "w1"}],
            stop_at=65.0,
        )
        panes = make_panes(tmp_path)
        token_calls = {str(tmp_path / "a"): 0, str(tmp_path / "b"): 0}

        def counting_token(ws):
            token_calls[ws["cwd"]] += 1
            return {"jj_status": "abc ●"}

        def fake_herdr(*args):
            if args[:2] == ("workspace", "list"):
                return {"workspaces": WORKSPACES_NO_CWD}
            if args[:2] == ("pane", "list"):
                return {"panes": panes}
            return {}

        with pytest.raises(StopLoop):
            reporter_module.run_loop(
                run_loop_env(tmp_path),
                connector=lambda path: events,
                clock=clock,
                sleep=lambda s: None,
                herdr_fn=fake_herdr,
                token_fn=counting_token,
            )

        assert token_calls[str(tmp_path / "a")] >= 12
        assert token_calls[str(tmp_path / "b")] <= 4


class TestRefreshOnce:
    def test_reports_all_jj_workspaces(self, tmp_path):
        calls = []
        panes = make_panes(tmp_path)

        def fake_herdr(*args):
            calls.append(args)
            if args[:2] == ("workspace", "list"):
                return {"workspaces": WORKSPACES_NO_CWD}
            if args[:2] == ("pane", "list"):
                return {"panes": panes}
            return {}

        rc = reporter_module.refresh_once(
            run_loop_env(tmp_path),
            herdr_fn=fake_herdr,
            token_fn=token_for(
                {
                    str(tmp_path / "a"): "abc ●",
                    str(tmp_path / "b"): JjError("no"),
                }
            ),
        )

        assert rc == 0
        reports = [c for c in calls if c[:2] == ("workspace", "report-metadata")]
        assert len(reports) == 1
        assert reports[0][2] == "w1"
        assert "jj_status=abc ●" in reports[0]
