from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

from PIL import Image


class FFmpegX11Capture:
    """Continuously drained XCB capture scaled before entering Python."""

    def __init__(
        self,
        executable: str,
        display_name: str,
        desktop_size: tuple[int, int],
    ) -> None:
        if not Path(executable).is_file():
            raise OSError("ffmpeg executable is unavailable")
        self.executable = executable
        self.display_name = display_name
        self.desktop_size = desktop_size
        self.target_size = desktop_size
        self.frames_per_second = 60.0
        self._process: subprocess.Popen[bytes] | None = None
        self._buffer = bytearray()

    def set_target_size(self, width: int, height: int) -> None:
        target = width, height
        if target != self.target_size:
            self.target_size = target
            self._stop()

    def set_frame_rate(self, frames_per_second: float) -> None:
        if not 0.5 <= frames_per_second <= 120.0:
            raise ValueError("capture FPS must be between 0.5 and 120")
        if frames_per_second != self.frames_per_second:
            self.frames_per_second = frames_per_second
            self._stop()

    def _start(self) -> None:
        if self._process is not None:
            return
        desktop_width, desktop_height = self.desktop_size
        width, height = self.target_size
        command = [
            self.executable,
            "-nostdin",
            "-loglevel",
            "error",
            "-thread_queue_size",
            "1",
            "-f",
            "x11grab",
            "-draw_mouse",
            "0",
            "-framerate",
            f"{self.frames_per_second:g}",
            "-video_size",
            f"{desktop_width}x{desktop_height}",
            "-i",
            f"{self.display_name}+0,0",
            "-vf",
            f"scale={width}:{height}:flags=bicubic",
            "-pix_fmt",
            "rgb24",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            close_fds=True,
        )
        self._buffer = bytearray(width * height * 3)

    def capture(self) -> tuple[Image.Image, int, bytes]:
        self._start()
        process = self._process
        if process is None or process.stdout is None:
            raise OSError("FFmpeg X11 capture did not start")
        view = memoryview(self._buffer)
        offset = 0
        while offset < len(view):
            count = process.stdout.readinto(view[offset:])
            if not count:
                detail = ""
                if process.stderr is not None:
                    detail = process.stderr.read(2048).decode(errors="replace").strip()
                self._stop()
                suffix = f": {detail}" if detail else ""
                raise OSError(f"FFmpeg X11 capture stream ended{suffix}")
            offset += count
        captured_ns = time.monotonic_ns()
        digest = hashlib.blake2s(self._buffer, digest_size=8).digest()
        image = Image.frombytes("RGB", self.target_size, self._buffer, "raw", "RGB", 0, 1)
        return image, captured_ns, digest

    def _stop(self) -> None:
        process = self._process
        self._process = None
        self._buffer = bytearray()
        if process is None:
            return
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        if process.stderr is not None:
            process.stderr.close()

    def close(self) -> None:
        self._stop()
