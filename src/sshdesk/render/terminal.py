from __future__ import annotations

from PIL import Image

from sshdesk.capture.base import Frame

from .base import Cell, FrameUpdate, RenderedFrame, Renderer, UpdateKind, Viewport


class TerminalRenderer(Renderer):
    """Render two vertical RGB pixels per terminal cell using Unicode half blocks."""

    def __init__(
        self,
        delta_full_threshold: float = 0.60,
        top_margin: int = 0,
        render_scale: float = 1.0,
    ) -> None:
        self.delta_full_threshold = delta_full_threshold
        if not 0 <= top_margin <= 16:
            raise ValueError("renderer top margin must be between 0 and 16")
        if not 0.25 <= render_scale <= 1.0:
            raise ValueError("renderer scale must be between 0.25 and 1.0")
        self.top_margin = top_margin
        self.render_scale = float(render_scale)

    @staticmethod
    def calculate_viewport(
        desktop_width: int,
        desktop_height: int,
        columns: int,
        rows: int,
        top_margin: int = 0,
        render_scale: float = 1.0,
    ) -> Viewport:
        if not 1 <= columns <= 1024 or not 1 <= rows <= 1024:
            raise ValueError("terminal dimensions must be between 1 and 1024")
        if desktop_width <= 0 or desktop_height <= 0:
            raise ValueError("desktop dimensions must be positive")
        if not 0.25 <= render_scale <= 1.0:
            raise ValueError("renderer scale must be between 0.25 and 1.0")

        # Keep at least one content row on unusually small terminals.
        margin = min(top_margin, max(0, rows - 1))
        content_rows = rows - margin
        # A terminal cell is represented by two vertical source pixels.
        scale = min(columns / desktop_width, (content_rows * 2) / desktop_height)
        scale *= render_scale
        pixel_width = max(1, min(columns, round(desktop_width * scale)))
        pixel_height = max(2, min(content_rows * 2, round(desktop_height * scale)))
        cell_height = max(1, (pixel_height + 1) // 2)
        x = (columns - pixel_width) // 2
        y = margin + (content_rows - cell_height) // 2
        return Viewport(x, y, pixel_width, cell_height, desktop_width, desktop_height)

    def render(self, frame: Frame, width: int, height: int) -> RenderedFrame:
        viewport = self.calculate_viewport(
            frame.width,
            frame.height,
            width,
            height,
            self.top_margin,
            self.render_scale,
        )
        image_height = viewport.height * 2
        target = (viewport.width, image_height)
        if frame.image.mode == "RGB" and frame.image.size == target:
            image = frame.image
        else:
            resampling = getattr(Image, "Resampling", Image)
            image = frame.image.convert("RGB").resize(target, resampling.BILINEAR)
        pixels = image.load()
        black = Cell((0, 0, 0), (0, 0, 0))
        cells = [black] * (width * height)
        for row in range(viewport.height):
            target = (viewport.y + row) * width + viewport.x
            top_y = row * 2
            bottom_y = min(top_y + 1, image_height - 1)
            for column in range(viewport.width):
                cells[target + column] = Cell(pixels[column, top_y], pixels[column, bottom_y])
        return RenderedFrame(width, height, viewport, tuple(cells))

    def target_size(
        self,
        desktop_width: int,
        desktop_height: int,
        width: int,
        height: int,
    ) -> tuple[int, int]:
        viewport = self.calculate_viewport(
            desktop_width,
            desktop_height,
            width,
            height,
            self.top_margin,
            self.render_scale,
        )
        return viewport.width, viewport.height * 2

    def diff(
        self, previous: RenderedFrame | None, current: RenderedFrame
    ) -> FrameUpdate:
        if previous is current:
            return FrameUpdate(UpdateKind.UNCHANGED, current)
        if (
            previous is None
            or previous.terminal_width != current.terminal_width
            or previous.terminal_height != current.terminal_height
            or previous.viewport != current.viewport
            or len(previous.cells) != len(current.cells)
        ):
            return FrameUpdate(UpdateKind.FULL, current)
        changes = tuple(
            (index, cell)
            for index, (old, cell) in enumerate(zip(previous.cells, current.cells))
            if old != cell
        )
        if not changes:
            return FrameUpdate(UpdateKind.UNCHANGED, current)
        if len(changes) / len(current.cells) >= self.delta_full_threshold:
            return FrameUpdate(UpdateKind.FULL, current)
        return FrameUpdate(UpdateKind.DELTA, current, changes)
