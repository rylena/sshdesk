from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time

from PIL import Image, ImageGrab
from Xlib import display

from .base import Frame, ScreenCapture
from .ffmpeg import FFmpegX11Capture
from .xshm import XShmCapture


class X11Capture(ScreenCapture):
    """X11 root-window capture.

    FFmpeg/XCB continuously drains renderer-sized frames for high refresh rates.
    MIT-SHM with native OpenCV scaling and Pillow/XCB remain fallbacks.
    """

    def __init__(self, display_name: str | None = None) -> None:
        self.display_name = display_name or os.environ.get("DISPLAY")
        if not self.display_name:
            raise RuntimeError("DISPLAY is not set; X11 capture is unavailable")
        self._display = display.Display(self.display_name)
        self._lock = threading.RLock()
        self._root = self._display.screen().root
        geometry = self._root.get_geometry()
        self._desktop_size = (int(geometry.width), int(geometry.height))
        self._target_size: tuple[int, int] | None = None
        self._frames_per_second = 60.0
        self._ffmpeg: FFmpegX11Capture | None = None
        self._shared: XShmCapture | None = None
        self._next_geometry_check = 0.0
        self._backend = os.environ.get("SSHDESK_X11_CAPTURE", "auto").strip().lower()
        if self._backend not in {"auto", "ffmpeg", "xshm", "pillow"}:
            self._display.close()
            raise RuntimeError("SSHDESK_X11_CAPTURE must be auto, ffmpeg, xshm, or pillow")
        self._ffmpeg_executable = shutil.which("ffmpeg")
        if self._backend == "ffmpeg" and self._ffmpeg_executable is None:
            self._display.close()
            raise RuntimeError("FFmpeg capture was requested but ffmpeg is not installed")
        self._ffmpeg_disabled = self._backend in {"xshm", "pillow"} or not self._ffmpeg_executable
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
            self._close_ffmpeg()
            self._close_shared()

    def set_target_size(self, width: int, height: int) -> None:
        if not 1 <= width <= 16384 or not 1 <= height <= 16384:
            raise ValueError("capture target dimensions must be between 1 and 16384")
        with self._lock:
            desktop_width, desktop_height = self._desktop_size
            target = min(width, desktop_width), min(height, desktop_height)
            self._target_size = target
            if self._ffmpeg is not None:
                self._ffmpeg.set_target_size(*target)

    def set_frame_rate(self, frames_per_second: float) -> None:
        if not 0.5 <= frames_per_second <= 120.0:
            raise ValueError("capture FPS must be between 0.5 and 120")
        with self._lock:
            self._frames_per_second = frames_per_second
            if self._ffmpeg is not None:
                self._ffmpeg.set_frame_rate(frames_per_second)

    def _close_ffmpeg(self) -> None:
        if self._ffmpeg is not None:
            self._ffmpeg.close()
            self._ffmpeg = None

    def _ffmpeg_capture(self) -> Frame:
        if self._ffmpeg is None:
            if self._ffmpeg_executable is None:
                raise OSError("ffmpeg executable is unavailable")
            self._ffmpeg = FFmpegX11Capture(
                self._ffmpeg_executable,
                self.display_name,
                self._desktop_size,
            )
            self._ffmpeg.set_target_size(*(self._target_size or self._desktop_size))
            self._ffmpeg.set_frame_rate(self._frames_per_second)
        image, captured_ns, digest = self._ffmpeg.capture()
        return Frame(image, captured_ns, *self._desktop_size, digest)

    def _close_shared(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None

    def _shared_capture(self) -> Frame:
        if self._shared is None:
            self._shared = XShmCapture(self.display_name, *self._desktop_size)
        result = self._shared.capture(self._target_size or self._desktop_size)
        return Frame(
            result.image,
            result.captured_ns,
            *self._desktop_size,
            result.content_digest,
        )

    def capture(self) -> Frame:
        with self._lock:
            self._refresh_geometry()
            if not self._ffmpeg_disabled:
                try:
                    return self._ffmpeg_capture()
                except OSError:
                    self._close_ffmpeg()
                    if self._backend == "ffmpeg":
                        raise
                    self._ffmpeg_disabled = True
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
            desktop_size = image.size
            self._desktop_size = desktop_size
            target = self._target_size
            if target is not None and image.size != target:
                image = image.resize(target, Image.Resampling.BICUBIC)
            digest = hashlib.blake2s(image.tobytes(), digest_size=8).digest()
            return Frame(image, time.monotonic_ns(), *desktop_size, digest)

    def size(self) -> tuple[int, int]:
        with self._lock:
            self._refresh_geometry()
            return self._desktop_size

    def cursor_position(self) -> tuple[int, int] | None:
        with self._lock:
            pointer = self._root.query_pointer()
            return int(pointer.root_x), int(pointer.root_y)

    def close(self) -> None:
        with self._lock:
            self._close_ffmpeg()
            self._close_shared()
            if self._display is not None:
                self._display.close()
                self._display = None
