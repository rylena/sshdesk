from .base import Frame, ScreenCapture
from .native import NativeCapture
from .synthetic import SyntheticCapture
from .wayland import WaylandCapture

__all__ = ["Frame", "NativeCapture", "ScreenCapture", "SyntheticCapture", "WaylandCapture"]
