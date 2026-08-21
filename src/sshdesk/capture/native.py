from __future__ import annotations

import hashlib
import platform
import time
from typing import Any

from PIL import Image, ImageGrab

from .base import Frame, ScreenCapture


def _quartz_bitmap_to_rgb(quartz: Any, cgimage: object) -> Image.Image:
    width = int(quartz.CGImageGetWidth(cgimage))
    height = int(quartz.CGImageGetHeight(cgimage))
    bytes_per_row = int(quartz.CGImageGetBytesPerRow(cgimage))
    provider = quartz.CGImageGetDataProvider(cgimage)
    copied = quartz.CGDataProviderCopyData(provider) if provider is not None else None
    if width < 1 or height < 1 or copied is None:
        raise RuntimeError("desktop capture failed; empty display image")
    raw = bytes(copied)
    if len(raw) < bytes_per_row * height:
        raise RuntimeError("desktop capture failed; incomplete pixel buffer")
    return (
        Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", bytes_per_row, 1)
        .convert("RGB")
        .copy()
    )


def grab_macos_display(quartz: Any) -> Image.Image:
    display = quartz.CGMainDisplayID()
    cgimage = quartz.CGDisplayCreateImage(display)
    if cgimage is None:
        raise RuntimeError(
            "desktop capture failed; grant Screen Recording permission to the SSH/Python process"
        )
    image = _quartz_bitmap_to_rgb(quartz, cgimage)
    logical = (
        int(quartz.CGDisplayPixelsWide(display)),
        int(quartz.CGDisplayPixelsHigh(display)),
    )
    if logical[0] >= 1 and logical[1] >= 1 and image.size != logical:
        return image.resize(logical, Image.Resampling.BICUBIC)
    return image


class NativeCapture(ScreenCapture):
    """Native desktop capture for Windows (Pillow) and macOS (Quartz)."""

    def __init__(self) -> None:
        self.system = platform.system()
        if self.system not in {"Windows", "Darwin"}:
            raise RuntimeError("native capture is available only on Windows and macOS")
        self._target_size: tuple[int, int] | None = None
        self._desktop_size = self._grab().size

    def _grab_macos(self) -> Image.Image:
        try:
            import Quartz
        except ImportError as exc:
            raise RuntimeError(
                "macOS capture needs the macOS extra: pip install 'sshdesk[macos]'"
            ) from exc
        return grab_macos_display(Quartz)

    def _grab(self) -> Image.Image:
        if self.system == "Darwin":
            return self._grab_macos()
        try:
            image = ImageGrab.grab(all_screens=True)
        except OSError as exc:
            raise RuntimeError(
                "desktop capture failed; run SSHDESK in the logged-in interactive Windows session"
            ) from exc
        if image.mode != "RGB":
            image = image.convert("RGB")
        return image

    def size(self) -> tuple[int, int]:
        return self._desktop_size

    def set_target_size(self, width: int, height: int) -> None:
        if not 1 <= width <= 16384 or not 1 <= height <= 16384:
            raise ValueError("capture target dimensions must be between 1 and 16384")
        self._target_size = width, height

    def capture(self) -> Frame:
        image = self._grab()
        desktop_size = image.size
        self._desktop_size = desktop_size
        target = self._target_size
        if target is not None and image.size != target:
            image = image.resize(target, Image.Resampling.BICUBIC)
        digest = hashlib.blake2s(image.tobytes(), digest_size=8).digest()
        return Frame(image, time.monotonic_ns(), *desktop_size, digest)
