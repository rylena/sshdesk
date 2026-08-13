from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from sshdesk.capture.gnome import GnomeScreenCastCapture
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


class GnomeCaptureTests(unittest.TestCase):
    def test_display_configuration_becomes_complete_desktop_area(self) -> None:
        capture = object.__new__(GnomeScreenCastCapture)
        monitor_a = ("DP-1", "vendor", "left", "1")
        monitor_b = ("eDP-1", "vendor", "right", "2")
        state = (
            1,
            [
                (monitor_a, [("mode-a", 1920, 1080, 60.0, 1.0, [], {"is-current": True})], {}),
                (monitor_b, [("mode-b", 2560, 1600, 60.0, 1.0, [], {"is-current": True})], {}),
            ],
            [
                (0, 0, 1.0, 0, True, [monitor_a], {}),
                (1920, 0, 2.0, 0, False, [monitor_b], {}),
            ],
            {"layout-mode": 1},
        )
        capture._call = lambda *_args, **_kwargs: state
        self.assertEqual(capture._desktop_area(), (0, 0, 3200, 1080))


if __name__ == "__main__":
    unittest.main()
