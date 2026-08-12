from __future__ import annotations

import os
import queue
import select
import signal
import sys
import threading
import time
from typing import Any

from sshdesk.capture.base import ScreenCapture
from sshdesk.input import (
    ControlEvent,
    ControlKind,
    InputBackend,
    KeyEvent,
    MouseButtonEvent,
    MouseMoveEvent,
    MouseScrollEvent,
    TerminalEventParser,
    TerminalReportEvent,
    translate_coordinates,
)
from sshdesk.render import (
    KittyRenderedFrame,
    KittyRenderer,
    KittyWriter,
    RenderedFrame,
    TerminalCapabilities,
    TerminalRenderer,
    TerminalWriter,
    probe_graphics,
    translate_pixel_coordinates,
)
from sshdesk.render.base import UpdateKind
from sshdesk.stats import SessionStats

from .frame_pump import LatestFramePump
from .terminal_state import TerminalState


class DirectSession:
    """Render pixels or ANSI cells and consume input through one ordinary SSH PTY."""

    def __init__(
        self,
        capture: ScreenCapture,
        input_backend: InputBackend,
        capabilities: TerminalCapabilities | None = None,
        max_fps: float | None = None,
    ) -> None:
        self.capture = capture
        self.input = input_backend
        self.renderer = TerminalRenderer()
        self.capabilities = capabilities or TerminalCapabilities.detect()
        self.writer = TerminalWriter(self.capabilities)
        self.parser = TerminalEventParser()
        self.stats = SessionStats()
        self.stop_event = threading.Event()
        self.show_stats = False
        self.current: RenderedFrame | KittyRenderedFrame | None = None
        self.pixel_mouse = False
        self._current_lock = threading.Lock()
        self._controls: queue.SimpleQueue[ControlKind | BaseException] = queue.SimpleQueue()
        self._last_activity = time.monotonic()
        self._input_thread: threading.Thread | None = None
        self._latency_probe_ns: int | None = None
        self._last_latency_probe = 0.0
        self._requested_max_fps = max_fps
        self._active_fps = 30.0
        self._frame_pump: LatestFramePump | None = None

    def _configure_renderer(self, input_fd: int, output_fd: int) -> None:
        mode = os.environ.get("SSHDESK_RENDER", "auto").strip().lower()
        if mode not in {"auto", "ansi", "kitty"}:
            raise RuntimeError("SSHDESK_RENDER must be auto, ansi, or kitty")
        if mode == "ansi":
            return
        columns, rows = TerminalState.size(output_fd)
        probe = probe_graphics(
            input_fd,
            output_fd,
            columns=columns,
            rows=rows,
        )
        if probe.usable:
            self.renderer = KittyRenderer(probe)
            self.writer = KittyWriter(self.capabilities, probe)
            self.pixel_mouse = probe.pixel_mouse
            return
        if mode == "kitty":
            if probe.kitty_graphics:
                raise RuntimeError("terminal supports Kitty graphics but reported no pixel geometry")
            raise RuntimeError(
                "terminal does not support Kitty graphics; use kitty, Ghostty, or WezTerm"
            )

    @staticmethod
    def _parse_max_fps(value: str | float | None, *, sharp: bool) -> float:
        if value is None or (isinstance(value, str) and value.strip().lower() == "auto"):
            return 60.0 if sharp else 30.0
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("SSHDESK_MAX_FPS must be auto or a number") from exc
        if not 1.0 <= parsed <= 120.0:
            raise RuntimeError("SSHDESK_MAX_FPS must be between 1 and 120")
        return parsed

    def _refresh_rate(self, now: float, activity: float) -> float:
        idle = now - activity
        if idle < 1.0:
            return self._active_fps
        if idle < 5.0:
            return min(self._active_fps, 30.0)
        return min(self._active_fps, 2.0)

    def _handle_event(self, event: object) -> bool:
        if isinstance(event, ControlEvent):
            if event.kind == ControlKind.EXIT:
                self.stop_event.set()
                return True
            if event.kind == ControlKind.TOGGLE_STATS:
                self._controls.put(event.kind)
                return True
        if isinstance(event, TerminalReportEvent):
            if self._latency_probe_ns is not None:
                self.stats.latency_ms = max(
                    0.0, (time.monotonic_ns() - self._latency_probe_ns) / 1e6
                )
                self._latency_probe_ns = None
            return False
        if isinstance(event, KeyEvent):
            self.input.key(event)
            if self._frame_pump is not None:
                self._frame_pump.set_frames_per_second(self._active_fps)
            return True
        if not isinstance(event, (MouseMoveEvent, MouseButtonEvent, MouseScrollEvent)):
            return False
        with self._current_lock:
            current = self.current
        if current is None:
            return False
        if self.pixel_mouse and isinstance(current, KittyRenderedFrame):
            point = translate_pixel_coordinates(
                event.column,
                event.row,
                current.pixel_viewport,
            )
        else:
            point = translate_coordinates(event.column, event.row, current.viewport)
        if point is None:
            return False
        x, y = point
        if isinstance(event, MouseMoveEvent):
            self.input.move(x, y)
        elif isinstance(event, MouseButtonEvent):
            self.input.button(event.button, event.pressed, x, y)
        elif isinstance(event, MouseScrollEvent):
            self.input.scroll(event.amount, x, y)
        else:
            return False
        if self._frame_pump is not None:
            self._frame_pump.set_frames_per_second(self._active_fps)
        return True

    def _input_loop(self, input_fd: int) -> None:
        """Read and inject input independently so slow frame writes cannot starve it."""
        try:
            while not self.stop_event.is_set():
                ready, _, _ = select.select((input_fd,), (), (), 0.05)
                if ready:
                    try:
                        data = os.read(input_fd, 4096)
                    except BlockingIOError:
                        continue
                    if not data:
                        self.stop_event.set()
                        return
                    self.stats.bytes_received += len(data)
                    events = self.parser.feed(data)
                else:
                    events = self.parser.flush()
                for event in events:
                    if self._handle_event(event):
                        self._last_activity = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - forward worker failures to main
            self._controls.put(exc)
            self.stop_event.set()

    def _draw_extras(
        self,
        output_fd: int,
        cursor: tuple[int, int] | None,
        *,
        draw_stats: bool,
    ) -> int:
        output = bytearray()
        if draw_stats and self.show_stats:
            output.extend(self.writer.overlay(self.stats.snapshot()))
        with self._current_lock:
            current = self.current
        if cursor is not None and current is not None:
            output.extend(self.writer.cursor(current, *cursor, True))
        if output:
            TerminalState.write_all(output_fd, bytes(output))
            self.stats.bytes_sent += len(output)
        return len(output)

    def run(self) -> int:
        previous: RenderedFrame | KittyRenderedFrame | None = None
        last_change = time.monotonic()
        last_stats_draw = 0.0
        last_cursor: tuple[int, int] | None = None
        frame_sequence = 0
        previous_handlers: dict[int, Any] = {}

        def stop_handler(*_: object) -> None:
            self.stop_event.set()

        for signal_name in ("SIGHUP", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is not None:
                previous_handlers[signal_value] = signal.getsignal(signal_value)
                signal.signal(signal_value, stop_handler)
        try:
            self._configure_renderer(sys.stdin.fileno(), sys.stdout.fileno())
            requested_fps: str | float | None = self._requested_max_fps
            if requested_fps is None:
                requested_fps = os.environ.get("SSHDESK_MAX_FPS", "auto")
            self._active_fps = self._parse_max_fps(
                requested_fps,
                sharp=isinstance(self.renderer, KittyRenderer),
            )
            with TerminalState(writer=self.writer) as terminal:
                dimensions = TerminalState.size(terminal.output_fd)
                desktop_dimensions = self.capture.size()
                self._frame_pump = LatestFramePump(self.capture, self._active_fps)
                self._frame_pump.set_target_size(
                    *self.renderer.target_size(*desktop_dimensions, *dimensions)
                )
                self._frame_pump.start()
                self._input_thread = threading.Thread(
                    target=self._input_loop,
                    args=(terminal.input_fd,),
                    name="sshdesk-terminal-input",
                    daemon=True,
                )
                self._input_thread.start()
                while not self.stop_event.is_set():
                    while True:
                        try:
                            control = self._controls.get_nowait()
                        except queue.Empty:
                            break
                        if isinstance(control, BaseException):
                            raise control
                        if control == ControlKind.TOGGLE_STATS:
                            self.show_stats = not self.show_stats
                            if not self.show_stats:
                                with self._current_lock:
                                    current = self.current
                                if current is not None:
                                    ansi = self.writer.full(current)
                                    TerminalState.write_all(terminal.output_fd, ansi)
                                    self.stats.bytes_sent += len(ansi)
                            self._draw_extras(
                                terminal.output_fd, last_cursor, draw_stats=self.show_stats
                            )

                    new_dimensions = TerminalState.size(terminal.output_fd)
                    if new_dimensions != dimensions:
                        dimensions = new_dimensions
                        self._frame_pump.set_target_size(
                            *self.renderer.target_size(*desktop_dimensions, *dimensions)
                        )
                        previous = None
                        with self._current_lock:
                            self.current = None
                        reset = b"\x1b[2J\x1b[H"
                        if isinstance(self.writer, KittyWriter):
                            reset = b"\x1b_Ga=d,d=A,q=2\x1b\\" + reset
                        TerminalState.write_all(terminal.output_fd, reset)

                    now = time.monotonic()
                    self._frame_pump.set_frames_per_second(
                        self._refresh_rate(now, max(self._last_activity, last_change))
                    )
                    captured_frame = self._frame_pump.latest_after(
                        frame_sequence,
                        timeout=0.05,
                    )
                    if captured_frame is None:
                        continue
                    frame_sequence = captured_frame.sequence
                    frame = captured_frame.frame
                    now = time.monotonic()
                    started = time.perf_counter_ns()
                    remote_dimensions = frame.width, frame.height
                    if remote_dimensions != desktop_dimensions:
                        desktop_dimensions = remote_dimensions
                        self._frame_pump.set_target_size(
                            *self.renderer.target_size(*desktop_dimensions, *dimensions)
                        )
                        previous = None
                    rendered = self.renderer.render(frame, *dimensions)
                    rendered_at = time.perf_counter_ns()
                    update = self.renderer.diff(previous, rendered)
                    diffed = time.perf_counter_ns()
                    previous = rendered
                    with self._current_lock:
                        self.current = rendered
                    self.stats.dimensions(dimensions, (frame.width, frame.height))
                    self.stats.capture_pipeline(
                        self._frame_pump.captured_frames,
                        self._frame_pump.dropped_frames,
                    )
                    cursor = self.capture.cursor_position()
                    cursor_changed = cursor != last_cursor
                    draw_stats = self.show_stats and now - last_stats_draw >= 1.0

                    if update.kind != UpdateKind.UNCHANGED:
                        ansi = self.writer.update(update)
                        encoded = time.perf_counter_ns()
                        TerminalState.write_all(terminal.output_fd, ansi)
                        self.stats.bytes_sent += len(ansi)
                        last_change = now
                        self.stats.record_frame(
                            capture_ms=captured_frame.capture_ms,
                            render_ms=(rendered_at - started) / 1e6,
                            diff_ms=(diffed - rendered_at) / 1e6,
                            encode_ms=(encoded - diffed) / 1e6,
                            changed_percentage=update.changed_percentage,
                            full=update.kind == UpdateKind.FULL,
                        )
                        # Frame updates can overwrite the overlay and move the terminal cursor.
                        self._draw_extras(
                            terminal.output_fd,
                            cursor,
                            draw_stats=self.show_stats,
                        )
                        last_stats_draw = now
                    elif cursor_changed or draw_stats:
                        self._draw_extras(
                            terminal.output_fd,
                            cursor,
                            draw_stats=draw_stats,
                        )
                        if draw_stats:
                            last_stats_draw = now
                    last_cursor = cursor

                    if now - self._last_latency_probe >= 1.0 and (
                        self._latency_probe_ns is None
                        or time.monotonic_ns() - self._latency_probe_ns > 5_000_000_000
                    ):
                        self._latency_probe_ns = time.monotonic_ns()
                        probe = self.writer.latency_probe()
                        TerminalState.write_all(terminal.output_fd, probe)
                        self.stats.bytes_sent += len(probe)
                        self._last_latency_probe = now
            return 0
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"sshdesk-server: {exc}", file=sys.stderr)
            return 1
        finally:
            self.stop_event.set()
            if self._frame_pump is not None:
                self._frame_pump.close()
                self._frame_pump = None
            if self._input_thread and self._input_thread is not threading.current_thread():
                self._input_thread.join(timeout=1.0)
            self.writer.close()
            self.input.close()
            self.capture.close()
            for signal_value, handler in previous_handlers.items():
                signal.signal(signal_value, handler)
