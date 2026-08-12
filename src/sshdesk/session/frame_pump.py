from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sshdesk.capture.base import Frame, ScreenCapture


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    frame: Frame
    sequence: int
    capture_ms: float


class LatestFramePump:
    """Capture on a paced worker and retain only the newest frame.

    A slow terminal or network must never create a stale framebuffer queue.
    Overwriting an unconsumed frame trades unnecessary work for lower latency.
    """

    def __init__(self, capture: ScreenCapture, frames_per_second: float) -> None:
        self.capture = capture
        self._condition = threading.Condition()
        self._frames_per_second = self._bounded_fps(frames_per_second)
        self._rate_generation = 0
        self._target_size: tuple[int, int] | None = None
        self._target_generation = 0
        self._latest: CapturedFrame | None = None
        self._sequence = 0
        self._delivered_sequence = 0
        self._stop = False
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None
        self.captured_frames = 0
        self.dropped_frames = 0

    @staticmethod
    def _bounded_fps(value: float) -> float:
        if not 0.5 <= value <= 120.0:
            raise ValueError("capture FPS must be between 0.5 and 120")
        return float(value)

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("frame pump is already started")
            if self._target_size is None:
                raise RuntimeError("frame pump target size is not configured")
            self._thread = threading.Thread(
                target=self._run,
                name="sshdesk-screen-capture",
                daemon=True,
            )
            self._thread.start()

    def set_frames_per_second(self, value: float) -> None:
        value = self._bounded_fps(value)
        with self._condition:
            if value == self._frames_per_second:
                return
            self._frames_per_second = value
            self._rate_generation += 1
            self._condition.notify_all()

    def set_target_size(self, width: int, height: int) -> None:
        if not 1 <= width <= 16384 or not 1 <= height <= 16384:
            raise ValueError("capture target dimensions must be between 1 and 16384")
        target = width, height
        with self._condition:
            if target == self._target_size:
                return
            self._target_size = target
            self._target_generation += 1
            # Never deliver a frame captured for the old terminal geometry.
            self._latest = None
            self._condition.notify_all()

    def latest_after(
        self,
        sequence: int,
        timeout: float = 0.02,
    ) -> CapturedFrame | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while (
                self._error is None
                and not self._stop
                and (self._latest is None or self._latest.sequence <= sequence)
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if self._error is not None:
                raise RuntimeError("screen capture worker failed") from self._error
            if self._latest is None or self._latest.sequence <= sequence:
                return None
            self._delivered_sequence = self._latest.sequence
            return self._latest

    def _run(self) -> None:
        last_started = 0.0
        applied_generation = -1
        applied_rate_generation = -1
        try:
            while True:
                with self._condition:
                    while True:
                        if self._stop:
                            return
                        target = self._target_size
                        generation = self._target_generation
                        rate_generation = self._rate_generation
                        target_changed = generation != applied_generation
                        rate_changed = rate_generation != applied_rate_generation
                        interval = 1.0 / self._frames_per_second
                        now = time.monotonic()
                        deadline = now if target_changed or rate_changed else last_started + interval
                        if now >= deadline:
                            break
                        self._condition.wait(deadline - now)
                    if target is None:
                        raise RuntimeError("frame pump target size disappeared")

                if target_changed:
                    self.capture.set_target_size(*target)
                    applied_generation = generation
                if rate_changed:
                    self.capture.set_frame_rate(self._frames_per_second)
                    applied_rate_generation = rate_generation

                last_started = time.monotonic()
                started_ns = time.perf_counter_ns()
                frame = self.capture.capture()
                capture_ms = (time.perf_counter_ns() - started_ns) / 1e6

                with self._condition:
                    # A resize during capture invalidates the just-captured image.
                    if generation != self._target_generation:
                        continue
                    self._sequence += 1
                    if (
                        self._latest is not None
                        and self._latest.sequence > self._delivered_sequence
                    ):
                        self.dropped_frames += 1
                    self.captured_frames += 1
                    self._latest = CapturedFrame(frame, self._sequence, capture_ms)
                    self._condition.notify_all()
        except Exception as exc:  # noqa: BLE001 - propagated to the session thread
            with self._condition:
                self._error = exc
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
