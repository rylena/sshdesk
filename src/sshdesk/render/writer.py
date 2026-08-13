from __future__ import annotations

import io
from functools import lru_cache

from sshdesk.stats.metrics import StatsSnapshot

from .base import FrameUpdate, RenderedFrame, UpdateKind
from .capabilities import ColorMode, TerminalCapabilities

CSI = "\x1b["


class TerminalWriter:
    """Encode rendered cells for true-color, 256-color, or basic ANSI terminals."""

    ANSI16_PALETTE = (
        (0, 0, 0),
        (205, 49, 49),
        (13, 188, 121),
        (229, 229, 16),
        (36, 114, 200),
        (188, 63, 188),
        (17, 168, 205),
        (229, 229, 229),
        (102, 102, 102),
        (241, 76, 76),
        (35, 209, 139),
        (245, 245, 67),
        (59, 142, 234),
        (214, 112, 214),
        (41, 184, 219),
        (255, 255, 255),
    )

    def __init__(
        self,
        capabilities: TerminalCapabilities | None = None,
        title: str = "SSHDESK",
    ) -> None:
        self.capabilities = capabilities or TerminalCapabilities.detect()
        self._active = False
        self.title = "".join(character for character in title if character.isprintable())[:255]

    def close(self) -> None:
        """Release optional encoder resources."""

    def _title_enter(self) -> str:
        # Xterm's title stack lets SSHDESK restore the client's previous title.
        return CSI + "22;0t" + f"\x1b]2;{self.title}\x1b\\"

    @staticmethod
    def _title_leave() -> str:
        return CSI + "23;0t"

    @staticmethod
    @lru_cache(maxsize=4096)
    def _ansi256(color: tuple[int, int, int]) -> int:
        red, green, blue = color
        cube = tuple(round(channel / 255 * 5) for channel in color)
        cube_rgb = tuple(0 if value == 0 else 55 + value * 40 for value in cube)
        cube_distance = sum((left - right) ** 2 for left, right in zip(color, cube_rgb))
        gray_level = min(23, max(0, round(((red + green + blue) / 3 - 8) / 10)))
        gray = 8 + gray_level * 10
        gray_distance = sum((channel - gray) ** 2 for channel in color)
        if gray_distance < cube_distance:
            return 232 + gray_level
        return 16 + 36 * cube[0] + 6 * cube[1] + cube[2]

    @classmethod
    @lru_cache(maxsize=4096)
    def _ansi16(cls, color: tuple[int, int, int]) -> int:
        return min(
            range(len(cls.ANSI16_PALETTE)),
            key=lambda index: sum(
                (left - right) ** 2 for left, right in zip(color, cls.ANSI16_PALETTE[index])
            ),
        )

    def _cell_style(
        self, foreground: tuple[int, int, int], background: tuple[int, int, int]
    ) -> tuple[tuple[object, ...], str, str]:
        glyph = "▀" if self.capabilities.unicode else " "
        if not self.capabilities.unicode:
            averaged = (
                (foreground[0] + background[0]) // 2,
                (foreground[1] + background[1]) // 2,
                (foreground[2] + background[2]) // 2,
            )
            background = averaged
            foreground = averaged
        style: tuple[object, ...]
        if self.capabilities.color == ColorMode.TRUECOLOR:
            style = (*foreground, *background)
            escape = (
                f"{CSI}38;2;{foreground[0]};{foreground[1]};{foreground[2]};"
                f"48;2;{background[0]};{background[1]};{background[2]}m"
            )
        elif self.capabilities.color == ColorMode.ANSI256:
            foreground_index = self._ansi256(foreground)
            background_index = self._ansi256(background)
            style = (foreground_index, background_index)
            escape = f"{CSI}38;5;{foreground_index};48;5;{background_index}m"
        else:
            foreground_index = self._ansi16(foreground)
            background_index = self._ansi16(background)
            foreground_code = (
                30 + foreground_index
                if foreground_index < 8
                else 90 + foreground_index - 8
            )
            background_code = (
                40 + background_index
                if background_index < 8
                else 100 + background_index - 8
            )
            style = (foreground_index, background_index)
            escape = f"{CSI}{foreground_code};{background_code}m"
        return style, escape, glyph

    def enter(self) -> bytes:
        self._active = True
        value = (
            self._title_enter()
            + CSI
            + "?1049h"
            + CSI
            + "?25l"
            + CSI
            + "2J"
            + CSI
            + "H"
        )
        if self.capabilities.mouse:
            value += CSI + "?1003h"
            if self.capabilities.sgr_mouse:
                value += CSI + "?1006h"
        return value.encode()

    def leave(self) -> bytes:
        self._active = False
        return (
            CSI
            + "0m"
            + CSI
            + "?25h"
            + CSI
            + "?1006l"
            + CSI
            + "?1003l"
            + CSI
            + "?1049l"
            + self._title_leave()
        ).encode()

    def header(self, width: int) -> bytes:
        width = max(1, width)
        label = f" SSHDESK | {self.title.removeprefix('SSHDESK - ')} "
        line = label[:width].center(width)
        _, escape, _ = self._cell_style((235, 240, 248), (28, 38, 52))
        return f"{CSI}1;1H{escape}{line}{CSI}0m".encode()

    def full(self, frame: RenderedFrame) -> bytes:
        output = io.StringIO()
        output.write(CSI + "H")
        previous = None
        for row in range(frame.terminal_height):
            output.write(f"{CSI}{row + 1};1H")
            start = row * frame.terminal_width
            for cell in frame.cells[start : start + frame.terminal_width]:
                style, escape, glyph = self._cell_style(cell.foreground, cell.background)
                if style != previous:
                    output.write(escape)
                    previous = style
                output.write(glyph)
        output.write(CSI + "0m")
        return output.getvalue().encode("utf-8")

    def delta(self, update: FrameUpdate) -> bytes:
        output = io.StringIO()
        previous = None
        previous_index = -2
        width = update.frame.terminal_width
        for index, cell in update.changes:
            row, column = divmod(index, width)
            if index != previous_index + 1 or column == 0:
                output.write(f"{CSI}{row + 1};{column + 1}H")
            style, escape, glyph = self._cell_style(cell.foreground, cell.background)
            if style != previous:
                output.write(escape)
                previous = style
            output.write(glyph)
            previous_index = index
        output.write(CSI + "0m")
        return output.getvalue().encode("utf-8")

    def update(self, update: FrameUpdate) -> bytes:
        if update.kind == UpdateKind.FULL:
            return self.full(update.frame)
        if update.kind == UpdateKind.DELTA:
            return self.delta(update)
        return b""

    def cursor(self, frame: RenderedFrame, remote_x: int, remote_y: int, visible: bool) -> bytes:
        if not visible:
            return (CSI + "?25l").encode()
        viewport = frame.viewport
        x = viewport.x + min(
            viewport.width - 1, max(0, remote_x * viewport.width // viewport.desktop_width)
        )
        y = viewport.y + min(
            viewport.height - 1, max(0, remote_y * viewport.height // viewport.desktop_height)
        )
        return f"{CSI}{y + 1};{x + 1}H{CSI}?25h".encode()

    @staticmethod
    def latency_probe() -> bytes:
        # ANSI Device Status Report: compatible terminals reply CSI row;column R.
        return (CSI + "6n").encode()

    def overlay(self, stats: StatsSnapshot) -> bytes:
        lines = (
            (
                f" SSHDESK  {stats.fps:4.1f} updates  {stats.captured_fps:4.1f} capture FPS  "
                f"{stats.changed_percentage:4.1f}% changed "
            ),
            (
                f" cap {stats.capture_ms:4.1f}  render {stats.render_ms:4.1f}  "
                f"diff {stats.diff_ms:4.1f}  enc {stats.encode_ms:4.1f}  "
                f"write {stats.write_ms:4.1f}  age {stats.frame_age_ms:4.1f} ms "
            ),
            (
                f" tx {stats.bytes_sent_per_second * 8 / 1000:,.0f}  "
                f"rx {stats.bytes_received_per_second * 8 / 1000:,.0f} Kbit/s  "
                f"total {stats.bytes_sent / 1024:,.0f}/"
                f"{stats.bytes_received / 1024:,.0f} KiB  "
                f"RTT {stats.latency_ms:4.1f} ms "
            ),
            (
                f" term {stats.terminal_width}x{stats.terminal_height}  "
                f"remote {stats.remote_width}x{stats.remote_height}  "
                f"full {stats.full_frames}  delta {stats.delta_frames}  "
                f"drop {stats.dropped_frames} "
            ),
        )
        output = io.StringIO()
        width = stats.terminal_width or 80
        height = stats.terminal_height or 24
        _, overlay_escape, _ = self._cell_style((255, 255, 255), (22, 28, 38))
        for row, line in enumerate(lines[: max(0, height - 1)], 2):
            output.write(f"{CSI}{row};1H{overlay_escape}{line[:width]}")
        output.write(CSI + "0m")
        return output.getvalue().encode()
