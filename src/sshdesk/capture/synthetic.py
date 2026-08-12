from __future__ import annotations

import time

from PIL import Image, ImageDraw

from .base import Frame, ScreenCapture


class SyntheticCapture(ScreenCapture):
    """Deterministic screen source for tests, demos, and benchmarks."""

    def __init__(self, width: int = 1280, height: int = 720, animate: bool = True) -> None:
        self.width = width
        self.height = height
        self.animate = animate
        self.frame_number = 0

    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def capture(self) -> Frame:
        image = Image.new("RGB", (self.width, self.height), (18, 22, 30))
        draw = ImageDraw.Draw(image)
        bar_height = max(12, self.height // 18)
        draw.rectangle((0, 0, self.width, bar_height), fill=(35, 42, 55))
        draw.text((max(2, self.width // 100), max(1, bar_height // 4)), "SSHDESK", fill=(230, 235, 245))
        margin_x = max(4, self.width // 20)
        margin_y = max(bar_height + 4, self.height // 8)
        draw.rectangle(
            (margin_x, margin_y, self.width - margin_x, self.height - margin_y // 2),
            fill=(48, 57, 73),
        )
        draw.rectangle(
            (margin_x * 2, margin_y + 4, self.width // 2, self.height // 2),
            fill=(43, 108, 176),
        )
        draw.text((margin_x * 2 + 4, margin_y + 8), "Terminal desktop", fill=(255, 255, 255))
        if self.animate:
            radius = max(3, min(self.width, self.height) // 30)
            x = margin_x + (self.frame_number * 13) % max(1, self.width - 2 * margin_x - 2 * radius)
            y = max(bar_height, self.height - margin_y - 2 * radius)
            draw.ellipse((x, y, x + 2 * radius, y + 2 * radius), fill=(238, 127, 74))
        self.frame_number += 1
        return Frame(image=image, captured_ns=time.monotonic_ns())
