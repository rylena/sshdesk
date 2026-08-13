from __future__ import annotations

import os
import platform
from dataclasses import dataclass

from sshdesk.capture.base import ScreenCapture
from sshdesk.input.base import InputBackend, NullInputBackend


@dataclass(frozen=True, slots=True)
class PlatformSelection:
    system: str
    session: str
    capture: str
    input: str


def detect_platform() -> PlatformSelection:
    system = platform.system()
    if system == "Linux":
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if os.environ.get("WAYLAND_DISPLAY") and session != "x11":
            desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            if "gnome" in desktop or "unity" in desktop:
                return PlatformSelection(system, "wayland", "gnome", "mutter")
            return PlatformSelection(system, "wayland", "wayland", "ydotool")
        if os.environ.get("DISPLAY"):
            return PlatformSelection(system, "x11", "x11", "x11")
        raise RuntimeError("no Linux graphical session found (DISPLAY or WAYLAND_DISPLAY)")
    if system == "Darwin":
        return PlatformSelection(system, "aqua", "native", "quartz")
    if system == "Windows":
        return PlatformSelection(system, "windows", "native", "sendinput")
    raise RuntimeError(f"unsupported server operating system: {system or 'unknown'}")


def create_capture(name: str = "auto", display: str | None = None) -> ScreenCapture:
    selected = detect_platform() if name == "auto" else None
    backend = selected.capture if selected is not None else name
    if backend == "x11":
        from sshdesk.capture.x11 import X11Capture

        return X11Capture(display)
    if backend == "wayland":
        from sshdesk.capture.wayland import WaylandCapture

        return WaylandCapture()
    if backend == "gnome":
        from sshdesk.capture.gnome import GnomeScreenCastCapture

        return GnomeScreenCastCapture()
    if backend == "native":
        from sshdesk.capture.native import NativeCapture

        return NativeCapture()
    if backend == "synthetic":
        from sshdesk.capture.synthetic import SyntheticCapture

        return SyntheticCapture()
    raise ValueError(f"unknown capture backend: {backend}")


def create_input(
    name: str = "auto",
    display: str | None = None,
    *,
    enabled: bool = True,
    capture: ScreenCapture | None = None,
) -> InputBackend:
    if not enabled:
        return NullInputBackend()
    selected = detect_platform() if name == "auto" else None
    backend = selected.input if selected is not None else name
    provider = getattr(capture, "create_input_backend", None)
    if backend == "mutter" and callable(provider):
        return provider()
    if backend == "x11":
        from sshdesk.input.x11 import X11Input

        return X11Input(display)
    if backend == "ydotool":
        from sshdesk.input.ydotool import YdotoolInput

        return YdotoolInput()
    if backend == "mutter":
        raise RuntimeError("Mutter input requires a linked GNOME capture session")
    if backend == "quartz":
        from sshdesk.input.macos import MacOSInput

        return MacOSInput()
    if backend == "sendinput":
        from sshdesk.input.windows import WindowsInput

        return WindowsInput()
    if backend == "none":
        return NullInputBackend()
    raise ValueError(f"unknown input backend: {backend}")
