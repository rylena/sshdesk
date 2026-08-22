from __future__ import annotations

import io
import subprocess
import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from sshdesk.capture.ffmpeg import FFmpegX11Capture
from sshdesk.capture.gnome import GnomeScreenCastCapture
from sshdesk.capture.native import NativeCapture, grab_macos_display
from sshdesk.capture.wayland import WaylandCapture


class _FakeQuartz:
    def __init__(
        self,
        *,
        raw: bytes,
        width: int,
        height: int,
        bytes_per_row: int,
        logical: tuple[int, int],
        cgimage: object = "cgimage",
    ) -> None:
        self.raw = raw
        self.width = width
        self.height = height
        self.bytes_per_row = bytes_per_row
        self.logical = logical
        self.cgimage = cgimage

    def CGMainDisplayID(self) -> int:
        return 1

    def CGDisplayCreateImage(self, _display: int) -> object | None:
        return self.cgimage

    def CGImageGetWidth(self, _image: object) -> int:
        return self.width

    def CGImageGetHeight(self, _image: object) -> int:
        return self.height

    def CGImageGetBytesPerRow(self, _image: object) -> int:
        return self.bytes_per_row

    def CGImageGetDataProvider(self, _image: object) -> object:
        return "provider"

    def CGDataProviderCopyData(self, _provider: object) -> bytes:
        return self.raw

    def CGDisplayPixelsWide(self, _display: int) -> int:
        return self.logical[0]

    def CGDisplayPixelsHigh(self, _display: int) -> int:
        return self.logical[1]


class NativeCaptureTests(unittest.TestCase):
    def test_macos_quartz_capture_resizes_to_logical_pixels(self) -> None:
        # 2x2 BGRA: red, green / blue, white. Logical size is 1x1.
        raw = bytes(
            [
                0, 0, 255, 255,
                0, 255, 0, 255,
                255, 0, 0, 255,
                255, 255, 255, 255,
            ]
        )
        image = grab_macos_display(
            _FakeQuartz(raw=raw, width=2, height=2, bytes_per_row=8, logical=(1, 1))
        )
        self.assertEqual(image.size, (1, 1))
        self.assertEqual(image.mode, "RGB")

    def test_macos_quartz_capture_keeps_matching_logical_size(self) -> None:
        raw = bytes([0, 0, 255, 255, 255, 255, 255, 255])
        image = grab_macos_display(
            _FakeQuartz(raw=raw, width=2, height=1, bytes_per_row=8, logical=(2, 1))
        )
        self.assertEqual(image.size, (2, 1))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

    def test_macos_quartz_missing_frame_asks_for_screen_recording(self) -> None:
        quartz = _FakeQuartz(
            raw=b"", width=0, height=0, bytes_per_row=0, logical=(1, 1), cgimage=None
        )
        with self.assertRaisesRegex(RuntimeError, "Screen Recording"):
            grab_macos_display(quartz)

    def test_macos_grab_does_not_call_pillow_screencapture(self) -> None:
        capture = object.__new__(NativeCapture)
        capture.system = "Darwin"
        capture._grab_macos = lambda: Image.new("RGB", (10, 5), (1, 2, 3))
        with patch("sshdesk.capture.native.ImageGrab.grab") as grab:
            image = capture._grab()
        grab.assert_not_called()
        self.assertEqual(image.size, (10, 5))


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


class FFmpegCaptureTests(unittest.TestCase):
    def _capture(self) -> FFmpegX11Capture:
        capture = object.__new__(FFmpegX11Capture)
        capture._stderr_chunks = deque(maxlen=8)
        capture._stderr_thread = None
        return capture

    def test_stderr_drain_keeps_tail_and_survives_closed_stream(self) -> None:
        capture = self._capture()
        stream = io.BytesIO(b"x" * 4096 + b"frame dropped")
        capture._stderr_chunks.clear()
        capture._drain_stderr(stream)
        detail = capture._stderr_detail()
        self.assertTrue(detail.endswith("frame dropped"))
        self.assertLessEqual(len(detail), 2048)

    def test_stderr_drain_ignores_read_errors(self) -> None:
        class BrokenStream:
            def read(self, _size: int) -> bytes:
                raise OSError("closed")

        capture = self._capture()
        capture._stderr_chunks.clear()
        capture._drain_stderr(BrokenStream())
        self.assertEqual(capture._stderr_detail(), "")
        capture._drain_stderr(None)

    def test_capture_reports_drained_stderr_after_stream_ends(self) -> None:
        capture = self._capture()
        capture._buffer = bytearray(10)
        capture._stderr_chunks.clear()
        capture._stderr_chunks.append(b"capturer error")
        capture._process = SimpleNamespace(
            stdout=io.BytesIO(b""),
            stderr=None,
            poll=lambda: 1,
            terminate=lambda: None,
        )

        with self.assertRaisesRegex(OSError, "stream ended: capturer error"):
            capture.capture()
        self.assertIsNone(capture._process)

    def test_set_frame_rate_bounds_are_enforced(self) -> None:
        capture = self._capture()
        capture.frames_per_second = 60.0
        capture.target_size = (64, 64)
        capture._stop = lambda: None
        capture.set_frame_rate(30.0)
        self.assertEqual(capture.frames_per_second, 30.0)
        with self.assertRaisesRegex(ValueError, "between 0.5 and 120"):
            capture.set_frame_rate(500.0)


if __name__ == "__main__":
    unittest.main()
