from .base import Frame, ScreenCapture
from .gnome import GnomeScreenCastCapture
from .native import NativeCapture
from .synthetic import SyntheticCapture
from .wayland import WaylandCapture

__all__ = [
    "Frame",
    "GnomeScreenCastCapture",
    "NativeCapture",
    "ScreenCapture",
    "SyntheticCapture",
    "WaylandCapture",
]
