from __future__ import annotations

import subprocess
import threading
import unittest
from unittest.mock import patch

from PIL import Image

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


class X11CaptureTests(unittest.TestCase):
    def test_pillow_fallback_prescales_and_fingerprints_frame(self) -> None:
        try:
            from sshdesk.capture.x11 import X11Capture
        except ImportError as exc:
            self.skipTest(f"python-xlib unavailable: {exc}")

        capture = object.__new__(X11Capture)
        capture._lock = threading.RLock()
        capture._ffmpeg_disabled = True
        capture._shared_disabled = True
        capture._target_size = (20, 10)
        capture._desktop_size = (100, 50)
        capture.display_name = ":0"
        capture._refresh_geometry = lambda: None

        with patch(
            "sshdesk.capture.x11.ImageGrab.grab",
            return_value=Image.new("RGB", (100, 50), (12, 34, 56)),
        ):
            frame = capture.capture()

        self.assertEqual(frame.image.size, (20, 10))
        self.assertEqual((frame.width, frame.height), (100, 50))
        self.assertIsNotNone(frame.content_digest)


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

    def test_close_stops_screen_and_remote_desktop_sessions(self) -> None:
        capture = object.__new__(GnomeScreenCastCapture)
        capture._lock = threading.RLock()
        capture._closed = False
        capture._pipeline = None
        capture._sink = None
        capture._signal_subscription = None
        capture._screen_session_path = "/screen/session"
        capture._remote_session_path = "/remote/session"
        capture._stream_path = "/screen/stream"
        capture._pipewire_node = 42
        capture._cursor_position = (10, 20)
        calls: list[tuple[str, str, str]] = []
        capture._call = lambda name, path, interface, method, **_kwargs: calls.append(
            (name, path, method)
        )

        capture.close()

        self.assertEqual(
            calls,
            [
                (capture.SCREENCAST_NAME, "/screen/session", "Stop"),
                (capture.REMOTE_NAME, "/remote/session", "Stop"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
