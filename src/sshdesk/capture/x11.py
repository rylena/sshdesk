from __future__ import annotations

import os
import time

from PIL import ImageGrab
from Xlib import display

from .base import Frame, ScreenCapture
from .xshm import XShmCapture


class X11Capture(ScreenCapture):
    """X11 root-window capture.

    MIT-SHM captures the current framebuffer on demand and native OpenCV code
    scales it to the renderer's target. Pillow/XCB remains the fallback for X
    servers without local shared-memory support.
    """

    def __init__(self, display_name: str | None = None) -> None:
        self.display_name = display_name or os.environ.get("DISPLAY")
        if not self.display_name:
            raise RuntimeError("DISPLAY is not set; X11 capture is unavailable")
        self._display = display.Display(self.display_name)
        self._root = self._display.screen().root
        geometry = self._root.get_geometry()
        self._desktop_size = (int(geometry.width), int(geometry.height))
        self._target_size: tuple[int, int] | None = None
        self._shared: XShmCapture | None = None
        self._next_geometry_check = 0.0
        self._backend = os.environ.get("SSHDESK_X11_CAPTURE", "auto").strip().lower()
        if self._backend not in {"auto", "xshm", "pillow"}:
            self._display.close()
            raise RuntimeError("SSHDESK_X11_CAPTURE must be auto, xshm, or pillow")
        self._shared_disabled = self._backend == "pillow"

    def _refresh_geometry(self) -> None:
        now = time.monotonic()
        if now < self._next_geometry_check:
            return
        self._next_geometry_check = now + 1.0
        geometry = self._root.get_geometry()
        size = int(geometry.width), int(geometry.height)
        if size != self._desktop_size:
            self._desktop_size = size
            self._close_shared()

    def set_target_size(self, width: int, height: int) -> None:
        if not 1 <= width <= 16384 or not 1 <= height <= 16384:
            raise ValueError("capture target dimensions must be between 1 and 16384")
        desktop_width, desktop_height = self._desktop_size
        target = min(width, desktop_width), min(height, desktop_height)
        self._target_size = target

    def _close_shared(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None

    def _shared_capture(self) -> Frame:
        if self._shared is None:
            self._shared = XShmCapture(self.display_name, *self._desktop_size)
        result = self._shared.capture(self._target_size or self._desktop_size)
        return Frame(result.image, result.captured_ns, *self._desktop_size)

    def capture(self) -> Frame:
        self._refresh_geometry()
        if not self._shared_disabled:
            try:
                return self._shared_capture()
            except OSError:
                self._close_shared()
                if self._backend == "xshm":
                    raise
                self._shared_disabled = True
        image = ImageGrab.grab(xdisplay=self.display_name)
        if image.mode != "RGB":
            image = image.convert("RGB")
        return Frame(image=image, captured_ns=time.monotonic_ns())

    def size(self) -> tuple[int, int]:
        self._refresh_geometry()
        return self._desktop_size

    def cursor_position(self) -> tuple[int, int] | None:
        pointer = self._root.query_pointer()
        return int(pointer.root_x), int(pointer.root_y)

    def close(self) -> None:
        self._close_shared()
        if self._display is not None:
            self._display.close()
            self._display = None
