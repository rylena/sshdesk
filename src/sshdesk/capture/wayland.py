from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from .base import Frame, ScreenCapture


class WaylandCapture(ScreenCapture):
    """Wayland capture using the compositor's non-interactive screenshot helper.

    grim covers wlroots compositors such as Sway and Hyprland. GNOME and KDE
    use their desktop screenshot tools. All commands use fixed argument vectors.
    """

    def __init__(self) -> None:
        if not os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError("WAYLAND_DISPLAY is not set; Wayland capture is unavailable")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if ("gnome" in desktop or "unity" in desktop) and shutil.which("gnome-screenshot"):
            self.backend = "gnome-screenshot"
        elif ("kde" in desktop or "plasma" in desktop) and shutil.which("spectacle"):
            self.backend = "spectacle"
        elif shutil.which("grim"):
            self.backend = "grim"
        elif shutil.which("gnome-screenshot"):
            self.backend = "gnome-screenshot"
        elif shutil.which("spectacle"):
            self.backend = "spectacle"
        else:
            raise RuntimeError(
                "Wayland capture needs grim (wlroots), gnome-screenshot (GNOME), "
                "or spectacle (KDE Plasma)"
            )
        self._target_size: tuple[int, int] | None = None
        first = self._grab()
        self._desktop_size = first.size

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5.0,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"{command[0]} Wayland capture timed out after {exc.timeout:g} seconds"
            ) from exc

    def _grab(self) -> Image.Image:
        if self.backend == "grim":
            result = self._run(["grim", "-c", "-t", "png", "-"])
            if result.returncode != 0:
                detail = result.stderr.decode(errors="replace").strip()
                raise RuntimeError(f"grim Wayland capture failed: {detail or 'unknown error'}")
            source: io.BytesIO | Path = io.BytesIO(result.stdout)
            with Image.open(source) as opened:
                return opened.convert("RGB")

        suffix = ".png"
        descriptor, name = tempfile.mkstemp(prefix="sshdesk-capture-", suffix=suffix)
        os.close(descriptor)
        path = Path(name)
        try:
            if self.backend == "gnome-screenshot":
                command = ["gnome-screenshot", "-f", str(path)]
            else:
                command = ["spectacle", "-b", "-n", "-o", str(path)]
            result = self._run(command)
            if result.returncode != 0:
                detail = result.stderr.decode(errors="replace").strip()
                raise RuntimeError(
                    f"{self.backend} Wayland capture failed: {detail or 'unknown error'}"
                )
            with Image.open(path) as opened:
                return opened.convert("RGB")
        finally:
            path.unlink(missing_ok=True)

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
