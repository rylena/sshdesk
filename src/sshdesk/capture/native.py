from __future__ import annotations

import hashlib
import platform
import time

from PIL import Image, ImageGrab

from .base import Frame, ScreenCapture


class NativeCapture(ScreenCapture):
    """Pillow-backed desktop capture for Windows and macOS."""

    def __init__(self) -> None:
        self.system = platform.system()
        if self.system not in {"Windows", "Darwin"}:
            raise RuntimeError("native capture is available only on Windows and macOS")
        self._target_size: tuple[int, int] | None = None
        self._desktop_size = self._grab().size

    def _grab(self) -> Image.Image:
        try:
            image = ImageGrab.grab(all_screens=self.system == "Windows")
        except OSError as exc:
            permission = (
                "grant Screen Recording permission to the SSH/Python process"
                if self.system == "Darwin"
                else "run SSHDESK in the logged-in interactive Windows session"
            )
            raise RuntimeError(f"desktop capture failed; {permission}") from exc
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
