from __future__ import annotations

import base64
import hashlib
import os
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image

from sshdesk.capture.base import Frame

from .base import UpdateKind, Viewport
from .capabilities import TerminalCapabilities
from .probe import GraphicsProbe
from .writer import CSI, TerminalWriter

CHUNK_SIZE = 4096
IMAGE_ID_BASE = 0x7600
CURSOR_IMAGE_ID = 0x47600
PLACEMENT_ID = 1
TILE_TARGET_PIXELS = 128


@dataclass(frozen=True, slots=True)
class PixelViewport:
    x: int
    y: int
    width: int
    height: int
    desktop_width: int
    desktop_height: int


@dataclass(frozen=True, slots=True)
class KittyTile:
    image_id: int
    column: int
    row: int
    width: int
    height: int
    digest: bytes
    rgb: bytes


@dataclass(frozen=True, slots=True)
class KittyRenderedFrame:
    terminal_width: int
    terminal_height: int
    viewport: Viewport
    pixel_viewport: PixelViewport
    cell_width: int
    cell_height: int
    tiles: tuple[KittyTile, ...]


@dataclass(frozen=True, slots=True)
class KittyFrameUpdate:
    kind: UpdateKind
    frame: KittyRenderedFrame
    changes: tuple[KittyTile, ...] = ()

    @property
    def changed_percentage(self) -> float:
        total = self.frame.pixel_viewport.width * self.frame.pixel_viewport.height
        if not total:
            return 0.0
        if self.kind == UpdateKind.FULL:
            return 100.0
        changed = sum(tile.width * tile.height for tile in self.changes)
        return min(100.0, changed * 100.0 / total)


def translate_pixel_coordinates(
    x: int, y: int, viewport: PixelViewport
) -> tuple[int, int] | None:
    if not (
        viewport.x <= x < viewport.x + viewport.width
        and viewport.y <= y < viewport.y + viewport.height
    ):
        return None
    local_x = x - viewport.x
    local_y = y - viewport.y
    remote_x = min(
        viewport.desktop_width - 1,
        int((local_x + 0.5) * viewport.desktop_width / viewport.width),
    )
    remote_y = min(
        viewport.desktop_height - 1,
        int((local_y + 0.5) * viewport.desktop_height / viewport.height),
    )
    return remote_x, remote_y


class KittyRenderer:
    """Render the desktop as changed RGB tiles in terminal pixel space."""

    def __init__(self, probe: GraphicsProbe) -> None:
        if not probe.usable:
            raise ValueError("Kitty rendering requires graphics and terminal pixel geometry")
        self.probe = probe

    def _layout(
        self,
        desktop_width: int,
        desktop_height: int,
        width: int,
        height: int,
    ) -> tuple[Viewport, PixelViewport, int, int]:
        if not 1 <= width <= 1024 or not 1 <= height <= 1024:
            raise ValueError("terminal dimensions must be between 1 and 1024")
        if desktop_width <= 0 or desktop_height <= 0:
            raise ValueError("desktop dimensions must be positive")
        cell_width = self.probe.cell_width
        cell_height = self.probe.cell_height
        usable_width = width * cell_width
        usable_height = height * cell_height
        scale = min(usable_width / desktop_width, usable_height / desktop_height)
        ideal_width = max(cell_width, desktop_width * scale)
        ideal_height = max(cell_height, desktop_height * scale)
        cell_columns = max(1, min(width, int(ideal_width // cell_width)))
        cell_rows = max(1, min(height, int(ideal_height // cell_height)))
        image_width = cell_columns * cell_width
        image_height = cell_rows * cell_height
        left = (width - cell_columns) // 2
        top = (height - cell_rows) // 2
        viewport = Viewport(
            left,
            top,
            cell_columns,
            cell_rows,
            desktop_width,
            desktop_height,
        )
        pixel_viewport = PixelViewport(
            left * cell_width,
            top * cell_height,
            image_width,
            image_height,
            desktop_width,
            desktop_height,
        )
        return viewport, pixel_viewport, cell_width, cell_height

    def target_size(
        self,
        desktop_width: int,
        desktop_height: int,
        width: int,
        height: int,
    ) -> tuple[int, int]:
        _viewport, pixels, _cell_width, _cell_height = self._layout(
            desktop_width, desktop_height, width, height
        )
        return pixels.width, pixels.height

    def render(self, frame: Frame, width: int, height: int) -> KittyRenderedFrame:
        viewport, pixel_viewport, cell_width, cell_height = self._layout(
            frame.width, frame.height, width, height
        )
        image_width = pixel_viewport.width
        image_height = pixel_viewport.height
        cell_columns = viewport.width
        cell_rows = viewport.height
        left = viewport.x
        top = viewport.y
        if frame.image.mode == "RGB" and frame.image.size == (image_width, image_height):
            image = frame.image
        else:
            resampling = getattr(Image, "Resampling", Image)
            image = frame.image.convert("RGB").resize(
                (image_width, image_height), resampling.LANCZOS
            )

        tile_columns = max(1, TILE_TARGET_PIXELS // cell_width)
        tile_rows = max(1, TILE_TARGET_PIXELS // cell_height)
        tiles: list[KittyTile] = []
        for tile_y, row in enumerate(range(0, cell_rows, tile_rows)):
            rows_here = min(tile_rows, cell_rows - row)
            for tile_x, column in enumerate(range(0, cell_columns, tile_columns)):
                columns_here = min(tile_columns, cell_columns - column)
                x = column * cell_width
                y = row * cell_height
                tile_width = columns_here * cell_width
                tile_height = rows_here * cell_height
                rgb = image.crop((x, y, x + tile_width, y + tile_height)).tobytes()
                digest = hashlib.blake2s(rgb, digest_size=8).digest()
                image_id = IMAGE_ID_BASE + tile_y * 1024 + tile_x
                tiles.append(
                    KittyTile(
                        image_id,
                        left + column,
                        top + row,
                        tile_width,
                        tile_height,
                        digest,
                        rgb,
                    )
                )
        return KittyRenderedFrame(
            width,
            height,
            viewport,
            pixel_viewport,
            cell_width,
            cell_height,
            tuple(tiles),
        )

    def diff(
        self,
        previous: KittyRenderedFrame | None,
        current: KittyRenderedFrame,
    ) -> KittyFrameUpdate:
        if (
            previous is None
            or previous.terminal_width != current.terminal_width
            or previous.terminal_height != current.terminal_height
            or previous.pixel_viewport != current.pixel_viewport
            or previous.cell_width != current.cell_width
            or previous.cell_height != current.cell_height
            or len(previous.tiles) != len(current.tiles)
        ):
            return KittyFrameUpdate(UpdateKind.FULL, current, current.tiles)
        changes = tuple(
            tile
            for old, tile in zip(previous.tiles, current.tiles)
            if old.image_id != tile.image_id or old.digest != tile.digest
        )
        if not changes:
            return KittyFrameUpdate(UpdateKind.UNCHANGED, current)
        return KittyFrameUpdate(UpdateKind.DELTA, current, changes)


class KittyWriter(TerminalWriter):
    """Encode real RGB pixels with the Kitty graphics protocol."""

    def __init__(
        self,
        capabilities: TerminalCapabilities,
        probe: GraphicsProbe,
    ) -> None:
        super().__init__(capabilities)
        self.probe = probe
        self._cursor_loaded = False
        self._encoder_pool: ThreadPoolExecutor | None = None

    def close(self) -> None:
        if self._encoder_pool is not None:
            self._encoder_pool.shutdown(wait=True, cancel_futures=True)
            self._encoder_pool = None

    def enter(self) -> bytes:
        self._active = True
        value = CSI + "?1049h" + CSI + "?25l" + CSI + "?7l"
        if self.capabilities.mouse:
            value += CSI + "?1003h" + CSI + "?1006h"
            if self.probe.pixel_mouse:
                value += CSI + "?1016h"
        return (value + CSI + "2J" + CSI + "H").encode()

    def leave(self) -> bytes:
        self._active = False
        self._cursor_loaded = False
        return (
            "\x1b_Ga=d,d=A,q=2\x1b\\"
            + CSI
            + "0m"
            + CSI
            + "?25h"
            + CSI
            + "?1016l"
            + CSI
            + "?1006l"
            + CSI
            + "?1003l"
            + CSI
            + "?7h"
            + CSI
            + "?1049l"
        ).encode()

    @staticmethod
    def _chunks(payload: bytes) -> tuple[bytes, ...]:
        return tuple(
            payload[index : index + CHUNK_SIZE]
            for index in range(0, len(payload), CHUNK_SIZE)
        )

    def _place_rgb(self, tile: KittyTile) -> bytes:
        compressed = zlib.compress(tile.rgb, level=1)
        encoded = base64.b64encode(compressed)
        chunks = self._chunks(encoded)
        output = bytearray(
            f"\x1b_Ga=d,d=i,i={tile.image_id},p={PLACEMENT_ID},q=2\x1b\\"
            f"{CSI}{tile.row + 1};{tile.column + 1}H".encode()
        )
        first = chunks[0] if chunks else b""
        more = 1 if len(chunks) > 1 else 0
        output.extend(
            f"\x1b_Ga=T,q=2,C=1,z=-1,f=24,i={tile.image_id},p={PLACEMENT_ID},"
            f"s={tile.width},v={tile.height},o=z,m={more};".encode()
        )
        output.extend(first)
        output.extend(b"\x1b\\")
        for index, chunk in enumerate(chunks[1:], 1):
            more = 1 if index < len(chunks) - 1 else 0
            output.extend(f"\x1b_Gm={more},q=2;".encode())
            output.extend(chunk)
            output.extend(b"\x1b\\")
        return bytes(output)

    def _frame(self, tiles: tuple[KittyTile, ...], *, clear: bool = False) -> bytes:
        output = bytearray()
        if self.probe.synchronized_output:
            output.extend((CSI + "?2026h").encode())
        if clear:
            self._cursor_loaded = False
            output.extend(b"\x1b_Ga=d,d=A,q=2\x1b\\")
            output.extend((CSI + "2J" + CSI + "H").encode())
        if len(tiles) >= 4:
            if self._encoder_pool is None:
                self._encoder_pool = ThreadPoolExecutor(
                    max_workers=min(4, os.cpu_count() or 1),
                    thread_name_prefix="sshdesk-zlib",
                )
            for packet in self._encoder_pool.map(self._place_rgb, tiles):
                output.extend(packet)
        else:
            for tile in tiles:
                output.extend(self._place_rgb(tile))
        if self.probe.synchronized_output:
            output.extend((CSI + "?2026l").encode())
        return bytes(output)

    def full(self, frame: KittyRenderedFrame) -> bytes:
        return self._frame(frame.tiles, clear=True)

    def delta(self, update: KittyFrameUpdate) -> bytes:
        return self._frame(update.changes)

    def update(self, update: KittyFrameUpdate) -> bytes:
        if update.kind == UpdateKind.FULL:
            return self.full(update.frame)
        if update.kind == UpdateKind.DELTA:
            return self.delta(update)
        return b""

    @staticmethod
    def _cursor_rgba() -> tuple[int, int, bytes]:
        width, height = 9, 13
        pixels = bytearray(width * height * 4)
        for y in range(height):
            for x in range(width):
                inside = x <= min(5, y // 2) or (5 <= y <= 9 and 3 <= x <= 7)
                if not inside:
                    continue
                edge = x == 0 or y == 0 or x == min(5, y // 2) or y in (5, 9)
                offset = (y * width + x) * 4
                color = (0, 0, 0, 255) if edge else (255, 255, 255, 255)
                pixels[offset : offset + 4] = bytes(color)
        return width, height, bytes(pixels)

    @staticmethod
    def _inline_image(
        image_id: int,
        column: int,
        row: int,
        x_offset: int,
        y_offset: int,
        width: int,
        height: int,
        rgba: bytes,
    ) -> bytes:
        encoded = base64.b64encode(rgba)
        chunks = KittyWriter._chunks(encoded)
        output = bytearray(
            f"\x1b_Ga=d,d=i,i={image_id},p={PLACEMENT_ID},q=2\x1b\\"
            f"{CSI}{row + 1};{column + 1}H".encode()
        )
        first = chunks[0] if chunks else b""
        more = 1 if len(chunks) > 1 else 0
        output.extend(
            f"\x1b_Ga=T,q=2,C=1,z=1,f=32,i={image_id},p={PLACEMENT_ID},"
            f"s={width},v={height},X={x_offset},Y={y_offset},m={more};".encode()
        )
        output.extend(first)
        output.extend(b"\x1b\\")
        for index, chunk in enumerate(chunks[1:], 1):
            more = 1 if index < len(chunks) - 1 else 0
            output.extend(f"\x1b_Gm={more},q=2;".encode())
            output.extend(chunk)
            output.extend(b"\x1b\\")
        return bytes(output)

    def cursor(
        self,
        frame: KittyRenderedFrame,
        remote_x: int,
        remote_y: int,
        visible: bool,
    ) -> bytes:
        if not visible:
            return f"\x1b_Ga=d,d=i,i={CURSOR_IMAGE_ID},p={PLACEMENT_ID},q=2\x1b\\".encode()
        viewport = frame.pixel_viewport
        px = viewport.x + min(
            viewport.width - 1,
            max(0, remote_x * viewport.width // viewport.desktop_width),
        )
        py = viewport.y + min(
            viewport.height - 1,
            max(0, remote_y * viewport.height // viewport.desktop_height),
        )
        column, x_offset = divmod(px, frame.cell_width)
        row, y_offset = divmod(py, frame.cell_height)
        if not self._cursor_loaded:
            self._cursor_loaded = True
            width, height, rgba = self._cursor_rgba()
            return self._inline_image(
                CURSOR_IMAGE_ID,
                column,
                row,
                x_offset,
                y_offset,
                width,
                height,
                rgba,
            )
        return (
            f"\x1b_Ga=d,d=i,i={CURSOR_IMAGE_ID},p={PLACEMENT_ID},q=2\x1b\\"
            f"{CSI}{row + 1};{column + 1}H"
            f"\x1b_Ga=p,q=2,C=1,z=1,i={CURSOR_IMAGE_ID},p={PLACEMENT_ID},"
            f"X={x_offset},Y={y_offset}\x1b\\"
        ).encode()
