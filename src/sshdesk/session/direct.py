from __future__ import annotations

import os
import queue
import select
import signal
import socket
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

WINDOWS_CONSOLE_KEYS = {
    "H": b"\x1b[A",
    "P": b"\x1b[B",
    "M": b"\x1b[C",
    "K": b"\x1b[D",
    "G": b"\x1b[H",
    "O": b"\x1b[F",
    "I": b"\x1b[5~",
    "Q": b"\x1b[6~",
    "R": b"\x1b[2~",
    "S": b"\x1b[3~",
    ";": b"\x1bOP",
    "<": b"\x1bOQ",
    "=": b"\x1bOR",
    ">": b"\x1bOS",
    "?": b"\x1b[15~",
    "@": b"\x1b[17~",
    "A": b"\x1b[18~",
    "B": b"\x1b[19~",
    "C": b"\x1b[20~",
    "D": b"\x1b[21~",
    "\x85": b"\x1b[23~",
    "\x86": b"\x1b[24~",
}


class DirectSession:
    """Render pixels or ANSI cells and consume input through one ordinary SSH PTY."""

    def __init__(
        self,
        capture: ScreenCapture,
        input_backend: InputBackend,
        capabilities: TerminalCapabilities | None = None,
        max_fps: float | None = None,
        render_scale: float | None = None,
    ) -> None:
        self.capture = capture
        self.input = input_backend
        self.capabilities = capabilities or TerminalCapabilities.detect()
        self.device_name = socket.gethostname() or "remote"
        self.session_title = f"SSHDESK - {self.device_name}"
        self.renderer = TerminalRenderer(top_margin=1)
        self.writer = TerminalWriter(self.capabilities, self.session_title)
        self.parser = TerminalEventParser()
        self.stats = SessionStats()
        self.stop_event = threading.Event()
        self._wake_event = threading.Event()
        self.show_stats = False
        self.current: RenderedFrame | KittyRenderedFrame | None = None
        self.pixel_mouse = False
        self._current_lock = threading.Lock()
        self._cursor_lock = threading.Lock()
        self._pending_cursor: tuple[int, int] | None = None
        self._controls: queue.SimpleQueue[ControlKind | BaseException] = queue.SimpleQueue()
        self._last_activity = time.monotonic()
        self._input_thread: threading.Thread | None = None
        self._latency_probe_ns: int | None = None
        self._last_latency_probe = 0.0
        self._requested_max_fps = max_fps
        self._requested_render_scale = render_scale
        self._auto_render_scale = True
        self._render_scale = 1.0
        self._last_scale_adjust = 0.0
        self._active_fps = 30.0
        self._frame_pump: LatestFramePump | None = None

    def _configure_renderer(self, input_fd: int, output_fd: int) -> None:
        mode = os.environ.get("SSHDESK_RENDER", "auto").strip().lower()
        if mode not in {"auto", "ansi", "kitty"}:
            raise RuntimeError("SSHDESK_RENDER must be auto, ansi, or kitty")
        requested_scale: str | float | None = self._requested_render_scale
        if requested_scale is None:
            requested_scale = os.environ.get("SSHDESK_SCALE", "auto")
        self._auto_render_scale = self._is_auto_render_scale(requested_scale)
        render_scale = self._parse_render_scale(requested_scale)
        self._render_scale = render_scale
        self.renderer = TerminalRenderer(top_margin=1, render_scale=render_scale)
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
            self.renderer = KittyRenderer(probe, top_margin=1, render_scale=render_scale)
            self.writer = KittyWriter(self.capabilities, probe, self.session_title)
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

    @staticmethod
    def _parse_render_scale(value: str | float | None) -> float:
        if value is None or (isinstance(value, str) and value.strip().lower() == "auto"):
            return 1.0
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("SSHDESK_SCALE must be auto or a number") from exc
        if not 0.25 <= parsed <= 1.0:
            raise RuntimeError("SSHDESK_SCALE must be between 0.25 and 1.0")
        return parsed

    @staticmethod
    def _is_auto_render_scale(value: str | float | None) -> bool:
        return value is None or (isinstance(value, str) and value.strip().lower() == "auto")

    @staticmethod
    def _next_auto_render_scale(
        current: float,
        *,
        latency_ms: float,
        write_ms: float,
    ) -> float:
        if latency_ms >= 250.0 or write_ms >= 30.0:
            return max(0.5, round(current * 0.75, 2))
        if latency_ms >= 100.0 or write_ms >= 14.0:
            return max(0.5, round(current * 0.85, 2))
        if current < 1.0 and latency_ms < 60.0 and 0.0 < write_ms < 8.0:
            return min(1.0, round(current * 1.08, 2))
        return current

    def _estimated_latency_ms(self, snapshot_latency_ms: float) -> float:
        latency_ms = snapshot_latency_ms
        if self._latency_probe_ns is not None:
            latency_ms = max(
                latency_ms,
                max(0.0, (time.monotonic_ns() - self._latency_probe_ns) / 1e6),
            )
        return latency_ms

    def _set_render_scale(self, value: float) -> None:
        self._render_scale = value
        render_scale = getattr(self.renderer, "render_scale", None)
        if render_scale is not None:
            self.renderer.render_scale = value

    def _maybe_adjust_render_scale(self, now: float) -> bool:
        if not self._auto_render_scale:
            return False
        snapshot = self.stats.snapshot()
        next_scale = self._next_auto_render_scale(
            self._render_scale,
            latency_ms=self._estimated_latency_ms(snapshot.latency_ms),
            write_ms=snapshot.write_ms,
        )
        if next_scale == self._render_scale:
            return False
        cooldown = 2.0 if next_scale < self._render_scale else 8.0
        if now - self._last_scale_adjust < cooldown:
            return False
        self._last_scale_adjust = now
        self._set_render_scale(next_scale)
        return True

    def _refresh_rate(self, now: float, activity: float) -> float:
        idle = now - activity
        if idle < 1.0:
            rate = self._active_fps
        elif idle < 5.0:
            rate = min(self._active_fps, 30.0)
        else:
            rate = min(self._active_fps, 2.0)
        snapshot = self.stats.snapshot()
        latency_ms = self._estimated_latency_ms(snapshot.latency_ms)
        return self._limit_for_terminal_backpressure(
            rate,
            latency_ms=latency_ms,
            write_ms=snapshot.write_ms,
        )

    @staticmethod
    def _limit_for_terminal_backpressure(
        refresh_rate: float,
        *,
        latency_ms: float,
        write_ms: float,
    ) -> float:
        rate = max(1.0, refresh_rate)
        if latency_ms >= 500.0:
            rate = min(rate, 10.0)
        elif latency_ms >= 250.0:
            rate = min(rate, 15.0)
        elif latency_ms >= 100.0:
            rate = min(rate, 30.0)
        if write_ms > 0.0:
            # SSH can accept bytes faster than the client terminal can decode
            # them, so write time is a conservative overload signal.
            rate = min(rate, 1000.0 / max(1.0, write_ms * 2.0))
        return max(1.0, rate)

    @staticmethod
    def _can_reuse_rendered_frame(
        previous: object | None,
        previous_digest: bytes | None,
        frame: object,
    ) -> bool:
        digest = getattr(frame, "content_digest", None)
        return previous is not None and digest is not None and digest == previous_digest

    def _handle_event(self, event: object) -> bool:
        if isinstance(event, ControlEvent):
            if event.kind == ControlKind.EXIT:
                self.stop_event.set()
                self._wake_event.set()
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
        # Cursor placement is independent of the captured framebuffer. Keep only
        # the newest coordinate so rapid terminal motion cannot build a queue.
        with self._cursor_lock:
            self._pending_cursor = x, y
        return True

    @staticmethod
    def _coalesce_mouse_moves(events: list[object]) -> list[object]:
        """Keep the newest motion in each uninterrupted run of mouse moves."""

        compacted: list[object] = []
        for event in events:
            if (
                isinstance(event, MouseMoveEvent)
                and compacted
                and isinstance(compacted[-1], MouseMoveEvent)
            ):
                compacted[-1] = event
            else:
                compacted.append(event)
        return compacted

    def _take_pending_cursor(self) -> tuple[int, int] | None:
        with self._cursor_lock:
            cursor = self._pending_cursor
            self._pending_cursor = None
        return cursor

    def _input_loop(self, input_fd: int) -> None:
        """Read and inject input independently so slow frame writes cannot starve it."""
        try:
            while not self.stop_event.is_set():
                if os.name == "nt":
                    import msvcrt

                    data = bytearray()
                    while msvcrt.kbhit() and len(data) < 4096:
                        character = msvcrt.getwch()
                        if character in {"\x00", "\xe0"} and msvcrt.kbhit():
                            data.extend(WINDOWS_CONSOLE_KEYS.get(msvcrt.getwch(), b""))
                        else:
                            data.extend(character.encode("utf-8", errors="replace"))
                    if data:
                        self.stats.bytes_received += len(data)
                        events = self.parser.feed(bytes(data))
                    else:
                        events = self.parser.flush()
                        self.stop_event.wait(0.01)
                else:
                    ready, _, _ = select.select((input_fd,), (), (), 0.05)
                    if ready:
                        try:
                            data = os.read(input_fd, 4096)
                        except BlockingIOError:
                            continue
                        if not data:
                            self.stop_event.set()
                            self._wake_event.set()
                            return
                        self.stats.bytes_received += len(data)
                        events = self.parser.feed(data)
                    else:
                        events = self.parser.flush()
                for event in self._coalesce_mouse_moves(events):
                    if self._handle_event(event):
                        self._last_activity = time.monotonic()
                        self._wake_event.set()
        except Exception as exc:  # noqa: BLE001 - forward worker failures to main
            self._controls.put(exc)
            self.stop_event.set()
            self._wake_event.set()

    def _draw_extras(
        self,
        output_fd: int,
        cursor: tuple[int, int] | None,
        *,
        draw_stats: bool,
        draw_header: bool = False,
    ) -> int:
        output = bytearray()
        if draw_header:
            with self._current_lock:
                current = self.current
            width = current.terminal_width if current is not None else 80
            output.extend(self.writer.header(width))
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

    def _reset_canvas(self, output_fd: int) -> None:
        reset = b"\x1b[2J\x1b[H"
        if isinstance(self.writer, KittyWriter):
            reset = self.writer.reset_canvas()
        TerminalState.write_all(output_fd, reset)
        self.stats.bytes_sent += len(reset)

    def run(self) -> int:
        previous: RenderedFrame | KittyRenderedFrame | None = None
        last_change = time.monotonic()
        last_stats_draw = 0.0
        last_cursor: tuple[int, int] | None = None
        frame_sequence = 0
        next_frame = 0.0
        last_content_digest: bytes | None = None
        previous_handlers: dict[int, Any] = {}

        def stop_handler(*_: object) -> None:
            self.stop_event.set()
            self._wake_event.set()

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
                                terminal.output_fd,
                                last_cursor,
                                draw_stats=self.show_stats,
                                draw_header=True,
                            )

                    # Do not wait for a desktop frame before moving the cursor.
                    # The terminal-side cursor update is tiny and mouse motion
                    # remains responsive even on an otherwise static desktop.
                    pending_cursor = self._take_pending_cursor()
                    if pending_cursor is not None and pending_cursor != last_cursor:
                        self._draw_extras(
                            terminal.output_fd,
                            pending_cursor,
                            draw_stats=False,
                        )
                        last_cursor = pending_cursor

                    new_dimensions = TerminalState.size(terminal.output_fd)
                    if new_dimensions != dimensions:
                        dimensions = new_dimensions
                        self._frame_pump.set_target_size(
                            *self.renderer.target_size(*desktop_dimensions, *dimensions)
                        )
                        previous = None
                        last_content_digest = None
                        with self._current_lock:
                            self.current = None
                        self._reset_canvas(terminal.output_fd)
                        next_frame = 0.0

                    now = time.monotonic()
                    if self._maybe_adjust_render_scale(now):
                        self._frame_pump.set_target_size(
                            *self.renderer.target_size(*desktop_dimensions, *dimensions)
                        )
                        previous = None
                        last_content_digest = None
                        with self._current_lock:
                            self.current = None
                        self._reset_canvas(terminal.output_fd)
                        next_frame = 0.0
                        continue

                    refresh_rate = self._refresh_rate(
                        now, max(self._last_activity, last_change)
                    )
                    if now < next_frame:
                        if self._wake_event.wait(min(next_frame - now, 0.05)):
                            self._wake_event.clear()
                            next_frame = 0.0
                        continue
                    captured_frame = self._frame_pump.latest_after(
                        frame_sequence,
                        timeout=0.05,
                    )
                    if captured_frame is None:
                        continue
                    frame_sequence = captured_frame.sequence
                    frame = captured_frame.frame
                    now = time.monotonic()
                    # Capture keeps running at the active rate. Only presentation
                    # slows while idle, avoiding a stale 2 FPS stream on input.
                    next_frame = now + 1.0 / refresh_rate
                    started = time.perf_counter_ns()
                    remote_dimensions = frame.width, frame.height
                    if remote_dimensions != desktop_dimensions:
                        desktop_dimensions = remote_dimensions
                        self._frame_pump.set_target_size(
                            *self.renderer.target_size(*desktop_dimensions, *dimensions)
                        )
                        previous = None
                        last_content_digest = None
                    if self._can_reuse_rendered_frame(
                        previous, last_content_digest, frame
                    ):
                        rendered = previous
                    else:
                        rendered = self.renderer.render(frame, *dimensions)
                    last_content_digest = frame.content_digest
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
                    cursor = self._take_pending_cursor()
                    if cursor is None:
                        cursor = self.capture.cursor_position()
                    cursor_changed = cursor != last_cursor
                    draw_stats = self.show_stats and now - last_stats_draw >= 1.0

                    if update.kind != UpdateKind.UNCHANGED:
                        ansi = self.writer.update(update)
                        encoded = time.perf_counter_ns()
                        TerminalState.write_all(terminal.output_fd, ansi)
                        written = time.perf_counter_ns()
                        self.stats.bytes_sent += len(ansi)
                        last_change = now
                        self.stats.record_frame(
                            capture_ms=captured_frame.capture_ms,
                            render_ms=(rendered_at - started) / 1e6,
                            diff_ms=(diffed - rendered_at) / 1e6,
                            encode_ms=(encoded - diffed) / 1e6,
                            write_ms=(written - encoded) / 1e6,
                            frame_age_ms=max(
                                0.0, (time.monotonic_ns() - frame.captured_ns) / 1e6
                            ),
                            changed_percentage=update.changed_percentage,
                            full=update.kind == UpdateKind.FULL,
                        )
                        # Frame updates can overwrite the overlay and move the terminal cursor.
                        self._draw_extras(
                            terminal.output_fd,
                            cursor,
                            draw_stats=self.show_stats,
                            draw_header=update.kind == UpdateKind.FULL,
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
        except KeyboardInterrupt:
            # Cleanup still runs via the finally block; 130 mirrors shell SIGINT convention.
            return 130
        finally:
            self.stop_event.set()
            self._wake_event.set()
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
