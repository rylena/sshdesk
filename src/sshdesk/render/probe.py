from __future__ import annotations

import fcntl
import os
import re
import select
import struct
import termios
import time
import tty
from dataclasses import dataclass

PROBE_IMAGE_ID = 0x765
PROBE_TIMEOUT = 0.75
KITTY_REPLY_RE = re.compile(rb"\x1b_G([^\x1b]*?)\x1b\\")
TEXT_AREA_RE = re.compile(rb"\x1b\[4;(\d{1,6});(\d{1,6})t")
CELL_SIZE_RE = re.compile(rb"\x1b\[6;(\d{1,4});(\d{1,4})t")
DA1_RE = re.compile(rb"\x1b\[\?[0-9;]*c")


@dataclass(frozen=True, slots=True)
class GraphicsProbe:
    kitty_graphics: bool = False
    pixel_mouse: bool = False
    synchronized_output: bool = False
    text_width: int = 0
    text_height: int = 0
    cell_width: int = 0
    cell_height: int = 0

    @property
    def usable(self) -> bool:
        return (
            self.kitty_graphics
            and self.text_width > 0
            and self.text_height > 0
            and self.cell_width > 0
            and self.cell_height > 0
        )


def _bounded(value: bytes, maximum: int) -> int:
    parsed = int(value)
    return parsed if 0 < parsed <= maximum else 0


def parse_graphics_probe(
    reply: bytes,
    *,
    columns: int,
    rows: int,
    ioctl_pixels: tuple[int, int] = (0, 0),
) -> GraphicsProbe:
    """Parse terminal replies without trusting their dimensions or payload sizes."""
    kitty = False
    needle = f"i={PROBE_IMAGE_ID}".encode()
    for match in KITTY_REPLY_RE.finditer(reply[:16384]):
        payload = match.group(1)
        if needle in payload and (b";OK" in payload or b"=OK" in payload):
            kitty = True
            break

    text_width, text_height = ioctl_pixels
    text_match = TEXT_AREA_RE.search(reply)
    if (text_width <= 0 or text_height <= 0) and text_match:
        height, width = text_match.groups()
        text_width = _bounded(width, 65535)
        text_height = _bounded(height, 65535)
    if not (0 < text_width <= 65535 and 0 < text_height <= 65535):
        text_width = text_height = 0

    cell_width = cell_height = 0
    cell_match = CELL_SIZE_RE.search(reply)
    if cell_match:
        height, width = cell_match.groups()
        cell_width = _bounded(width, 1024)
        cell_height = _bounded(height, 1024)
    if not cell_width and text_width and columns:
        cell_width = max(1, text_width // columns)
    if not cell_height and text_height and rows:
        cell_height = max(1, text_height // rows)

    pixel_mouse = bool(re.search(rb"\x1b\[\?1016;[123]\$y", reply))
    synchronized = bool(re.search(rb"\x1b\[\?2026;[123]\$y", reply))
    return GraphicsProbe(
        kitty_graphics=kitty,
        pixel_mouse=pixel_mouse,
        synchronized_output=synchronized,
        text_width=text_width,
        text_height=text_height,
        cell_width=cell_width,
        cell_height=cell_height,
    )


def ioctl_pixel_size(fd: int) -> tuple[int, int]:
    try:
        packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        _rows, _columns, width, height = struct.unpack("HHHH", packed)
    except (OSError, struct.error):
        return 0, 0
    return width, height


def probe_graphics(
    input_fd: int,
    output_fd: int,
    *,
    columns: int,
    rows: int,
    timeout: float = PROBE_TIMEOUT,
) -> GraphicsProbe:
    """Probe the terminal through the SSH PTY before the input thread starts."""
    if not os.isatty(input_fd) or not os.isatty(output_fd):
        return GraphicsProbe()
    attributes = termios.tcgetattr(input_fd)
    payload = (
        f"\x1b_Gi={PROBE_IMAGE_ID},s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\"
        "\x1b[14t"
        "\x1b[16t"
        "\x1b[?1016$p"
        "\x1b[?2026$p"
        "\x1b[c"
    ).encode()
    reply = bytearray()
    try:
        tty.setraw(input_fd, termios.TCSANOW)
        view = memoryview(payload)
        while view:
            written = os.write(output_fd, view)
            view = view[written:]
        deadline = time.monotonic() + max(0.05, min(timeout, 2.0))
        while len(reply) < 16384:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select((input_fd,), (), (), remaining)
            if not ready:
                break
            chunk = os.read(input_fd, min(4096, 16384 - len(reply)))
            if not chunk:
                break
            reply.extend(chunk)
            if DA1_RE.search(reply):
                break
    finally:
        termios.tcsetattr(input_fd, termios.TCSANOW, attributes)
    return parse_graphics_probe(
        bytes(reply),
        columns=columns,
        rows=rows,
        ioctl_pixels=ioctl_pixel_size(output_fd),
    )
