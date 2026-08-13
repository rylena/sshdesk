from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    fps: float = 0.0
    captured_fps: float = 0.0
    capture_ms: float = 0.0
    render_ms: float = 0.0
    diff_ms: float = 0.0
    encode_ms: float = 0.0
    write_ms: float = 0.0
    frame_age_ms: float = 0.0
    changed_percentage: float = 0.0
    latency_ms: float = 0.0
    bytes_sent_per_second: float = 0.0
    bytes_received_per_second: float = 0.0
    bytes_sent: int = 0
    bytes_received: int = 0
    terminal_width: int = 0
    terminal_height: int = 0
    remote_width: int = 0
    remote_height: int = 0
    full_frames: int = 0
    delta_frames: int = 0
    captured_frames: int = 0
    dropped_frames: int = 0

class SessionStats:
    """Low-overhead rolling performance instrumentation."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self._frame_times: deque[float] = deque()
        self._capture_ms: deque[float] = deque(maxlen=60)
        self._render_ms: deque[float] = deque(maxlen=60)
        self._diff_ms: deque[float] = deque(maxlen=60)
        self._encode_ms: deque[float] = deque(maxlen=60)
        self._write_ms: deque[float] = deque(maxlen=60)
        self._frame_age_ms: deque[float] = deque(maxlen=60)
        self._changed: deque[float] = deque(maxlen=60)
        self.bytes_sent = 0
        self.bytes_received = 0
        self.terminal_width = 0
        self.terminal_height = 0
        self.remote_width = 0
        self.remote_height = 0
        self.full_frames = 0
        self.delta_frames = 0
        self.captured_frames = 0
        self.dropped_frames = 0
        self.latency_ms = 0.0
        self._last_rate_time = self.started
        self._last_rate_sent = 0
        self._last_rate_received = 0
        self._sent_rate = 0.0
        self._received_rate = 0.0
        self._last_rate_captured = 0
        self._capture_rate = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _average(values: deque[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def record_frame(
        self,
        *,
        capture_ms: float,
        render_ms: float,
        diff_ms: float,
        encode_ms: float,
        write_ms: float,
        frame_age_ms: float,
        changed_percentage: float,
        full: bool,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._frame_times.append(now)
            while self._frame_times and self._frame_times[0] < now - 1.0:
                self._frame_times.popleft()
            self._capture_ms.append(capture_ms)
            self._render_ms.append(render_ms)
            self._diff_ms.append(diff_ms)
            self._encode_ms.append(encode_ms)
            self._write_ms.append(write_ms)
            self._frame_age_ms.append(frame_age_ms)
            self._changed.append(changed_percentage)
            if full:
                self.full_frames += 1
            else:
                self.delta_frames += 1

    def dimensions(self, terminal: tuple[int, int], remote: tuple[int, int]) -> None:
        with self._lock:
            self.terminal_width, self.terminal_height = terminal
            self.remote_width, self.remote_height = remote

    def capture_pipeline(self, captured: int, dropped: int) -> None:
        with self._lock:
            self.captured_frames = max(0, captured)
            self.dropped_frames = max(0, dropped)

    def snapshot(self) -> StatsSnapshot:
        with self._lock:
            now = time.monotonic()
            while self._frame_times and self._frame_times[0] < now - 1.0:
                self._frame_times.popleft()
            elapsed = now - self._last_rate_time
            if elapsed >= 0.25:
                self._sent_rate = (self.bytes_sent - self._last_rate_sent) / elapsed
                self._received_rate = (self.bytes_received - self._last_rate_received) / elapsed
                self._capture_rate = (
                    self.captured_frames - self._last_rate_captured
                ) / elapsed
                self._last_rate_sent = self.bytes_sent
                self._last_rate_received = self.bytes_received
                self._last_rate_captured = self.captured_frames
                self._last_rate_time = now
            return StatsSnapshot(
                fps=float(len(self._frame_times)),
                captured_fps=self._capture_rate,
                capture_ms=self._average(self._capture_ms),
                render_ms=self._average(self._render_ms),
                diff_ms=self._average(self._diff_ms),
                encode_ms=self._average(self._encode_ms),
                write_ms=self._average(self._write_ms),
                frame_age_ms=self._average(self._frame_age_ms),
                changed_percentage=self._average(self._changed),
                latency_ms=self.latency_ms,
                bytes_sent_per_second=self._sent_rate,
                bytes_received_per_second=self._received_rate,
                bytes_sent=self.bytes_sent,
                bytes_received=self.bytes_received,
                terminal_width=self.terminal_width,
                terminal_height=self.terminal_height,
                remote_width=self.remote_width,
                remote_height=self.remote_height,
                full_frames=self.full_frames,
                delta_frames=self.delta_frames,
                captured_frames=self.captured_frames,
                dropped_frames=self.dropped_frames,
            )
