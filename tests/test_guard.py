import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_jj.lib import guard


class ConfigDirTests(unittest.TestCase):
    def test_respects_xdg_config_home(self):
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(
                guard.config_dir(),
                Path("/tmp/xdg/herdr/plugins/config/aamini.cd-guard"),
            )


class ArmDisarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(guard, "config_dir", return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, name: str) -> list[str]:
        path = Path(self.tmp.name) / name
        return path.read_text().splitlines() if path.exists() else []

    def test_arm_writes_entry(self):
        guard.arm("w1", Path("/ws/one"))
        self.assertEqual(self.read("roots"), ["w1\t/ws/one"])

    def test_arm_replaces_existing_id(self):
        guard.arm("w1", Path("/ws/one"))
        guard.arm("w1", Path("/ws/two"))
        self.assertEqual(self.read("roots"), ["w1\t/ws/two"])

    def test_arm_prunes_dead_ids(self):
        guard.arm("w1", Path("/ws/one"))
        guard.arm("w2", Path("/ws/two"))
        guard.arm("w3", Path("/ws/three"), live_ids={"w2", "w3"})
        self.assertEqual(self.read("roots"), ["w2\t/ws/two", "w3\t/ws/three"])

    def test_disarm_removes_roots_and_disabled(self):
        guard.arm("w1", Path("/ws/one"))
        (Path(self.tmp.name) / "disabled").write_text("w1\nw2\n")
        guard.disarm("w1")
        self.assertEqual(self.read("roots"), [])
        self.assertEqual(self.read("disabled"), ["w2"])

    def test_prune_drops_dead_ids_from_both_files(self):
        guard.arm("w1", Path("/ws/one"))
        guard.arm("w2", Path("/ws/two"))
        (Path(self.tmp.name) / "disabled").write_text("w1\nw2\n")
        guard.prune({"w2"})
        self.assertEqual(self.read("roots"), ["w2\t/ws/two"])
        self.assertEqual(self.read("disabled"), ["w2"])


if __name__ == "__main__":
    unittest.main()
