from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from sshdesk.capture.wayland import WaylandCapture


class WaylandCaptureTests(unittest.TestCase):
    def test_capture_helper_timeout_becomes_clean_runtime_error(self) -> None:
        command = ["gnome-screenshot", "-f", "/tmp/frame.png"]
        with patch(
            "sshdesk.capture.wayland.subprocess.run",
            side_effect=subprocess.TimeoutExpired(command, 5.0),
        ), self.assertRaisesRegex(
            RuntimeError,
            "gnome-screenshot Wayland capture timed out after 5 seconds",
        ):
            WaylandCapture._run(command)


if __name__ == "__main__":
    unittest.main()
