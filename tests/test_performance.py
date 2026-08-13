from __future__ import annotations

import threading
import time
import unittest

from PIL import Image

from sshdesk.capture.base import Frame, ScreenCapture
from sshdesk.session.direct import DirectSession
from sshdesk.session.frame_pump import LatestFramePump


class _FastCapture(ScreenCapture):
    def __init__(self) -> None:
        self.target = (16, 9)
        self.calls = 0
        self.rates: list[float] = []

    def set_target_size(self, width: int, height: int) -> None:
        self.target = width, height

    def size(self) -> tuple[int, int]:
        return 1920, 1080

    def set_frame_rate(self, frames_per_second: float) -> None:
        self.rates.append(frames_per_second)

    def capture(self) -> Frame:
        self.calls += 1
        color = self.calls % 255
        return Frame(
            Image.new("RGB", self.target, (color, color, color)),
            time.monotonic_ns(),
            1920,
            1080,
        )


class _BlockingCapture(_FastCapture):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def capture(self) -> Frame:
        target = self.target
        if self.calls == 0:
            self.started.set()
            self.release.wait(1.0)
        self.calls += 1
        return Frame(Image.new("RGB", target), time.monotonic_ns(), 1920, 1080)


class PerformanceTests(unittest.TestCase):
    def test_frame_pump_keeps_latest_instead_of_queueing(self) -> None:
        capture = _FastCapture()
        pump = LatestFramePump(capture, 120)
        pump.set_target_size(16, 9)
        pump.start()
        try:
            first = pump.latest_after(0, timeout=1.0)
            self.assertIsNotNone(first)
            assert first is not None
            deadline = time.monotonic() + 1.0
            while pump.captured_frames < first.sequence + 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            newest = pump.latest_after(first.sequence, timeout=1.0)
            self.assertIsNotNone(newest)
            assert newest is not None
            self.assertGreater(newest.sequence, first.sequence + 1)
            self.assertGreater(pump.dropped_frames, 0)
        finally:
            pump.close()

    def test_resize_discards_in_flight_old_geometry(self) -> None:
        capture = _BlockingCapture()
        pump = LatestFramePump(capture, 120)
        pump.set_target_size(16, 9)
        pump.start()
        try:
            self.assertTrue(capture.started.wait(1.0))
            pump.set_target_size(32, 18)
            capture.release.set()
            frame = pump.latest_after(0, timeout=1.0)
            self.assertIsNotNone(frame)
            assert frame is not None
            self.assertEqual(frame.frame.image.size, (32, 18))
        finally:
            capture.release.set()
            pump.close()

    def test_sharp_mode_defaults_to_60_fps_with_bounds(self) -> None:
        self.assertEqual(DirectSession._parse_max_fps("auto", sharp=True), 60.0)
        self.assertEqual(DirectSession._parse_max_fps(None, sharp=False), 30.0)
        self.assertEqual(DirectSession._parse_max_fps("90", sharp=True), 90.0)
        with self.assertRaisesRegex(RuntimeError, "between 1 and 120"):
            DirectSession._parse_max_fps("240", sharp=True)

    def test_identical_capture_digest_reuses_rendered_state(self) -> None:
        image = Image.new("RGB", (16, 9))
        previous = object()
        same = Frame(image, 1, content_digest=b"same")
        changed = Frame(image, 2, content_digest=b"changed")
        unknown = Frame(image, 3)
        self.assertTrue(
            DirectSession._can_reuse_rendered_frame(previous, b"same", same)
        )
        self.assertFalse(
            DirectSession._can_reuse_rendered_frame(previous, b"same", changed)
        )
        self.assertFalse(
            DirectSession._can_reuse_rendered_frame(previous, b"same", unknown)
        )

    def test_frame_rate_change_reaches_capture_backend(self) -> None:
        capture = _FastCapture()
        pump = LatestFramePump(capture, 120)
        pump.set_target_size(16, 9)
        pump.start()
        try:
            self.assertIsNotNone(pump.latest_after(0, timeout=1.0))
            pump.set_frames_per_second(10)
            deadline = time.monotonic() + 1.0
            while 10.0 not in capture.rates and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertIn(120.0, capture.rates)
            self.assertIn(10.0, capture.rates)
        finally:
            pump.close()


if __name__ == "__main__":
    unittest.main()
