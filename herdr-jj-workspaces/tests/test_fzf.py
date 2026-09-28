import subprocess
from unittest.mock import patch

from herdr_jj.lib import fzf as fzf_module


def completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


class TestFzfSelect:
    def test_enter_returns_selected_line(self):
        with patch.object(
            subprocess, "run", return_value=completed("feat\tabc ✓\tfeat\n")
        ) as run:
            key, line = fzf_module.fzf_select(["feat\tabc ✓\tfeat"])
        assert key == "enter"
        assert line == "feat\tabc ✓\tfeat"
        assert run.call_args.kwargs["input"] == "feat\tabc ✓\tfeat"
        assert not any(arg.startswith("--expect=") for arg in run.call_args.args[0])

    def test_expects_key_binds(self):
        with patch.object(
            subprocess, "run", return_value=completed("ctrl-d\nfeat\n")
        ) as run:
            key, line = fzf_module.fzf_select(["feat"], expect=("ctrl-d", "ctrl-n"))
        assert (key, line) == ("ctrl-d", "feat")
        assert any(arg == "--expect=ctrl-d,ctrl-n" for arg in run.call_args.args[0])

    def test_cancel_exits_cleanly(self):
        with patch.object(subprocess, "run", return_value=completed("", 130)):
            key, line = fzf_module.fzf_select(["feat"])
        assert (key, line) == ("esc", None)

    def test_preview_passed_through(self):
        with patch.object(subprocess, "run", return_value=completed("x\n")) as run:
            fzf_module.fzf_select(["x"], preview="jj log")
        cmd = run.call_args.args[0]
        assert "--preview" in cmd
        assert "jj log" in cmd
