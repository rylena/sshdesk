from __future__ import annotations

import ctypes
import ctypes.util
import os
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image

IPC_CREAT = 0o1000
IPC_PRIVATE = 0
IPC_RMID = 0
ZPIXMAP = 2
ALL_PLANES = ctypes.c_ulong(-1).value


class _XImage(ctypes.Structure):
    # Prefix of XImage through the channel masks. Xlib owns the remaining
    # fields; these are all that SSHDESK needs.
    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
    ]


class _XShmSegmentInfo(ctypes.Structure):
    _fields_ = [
        ("shmseg", ctypes.c_ulong),
        ("shmid", ctypes.c_int),
        ("shmaddr", ctypes.c_void_p),
        ("read_only", ctypes.c_int),
    ]


@dataclass(frozen=True, slots=True)
class XShmFrame:
    image: Image.Image
    captured_ns: int


class XShmCapture:
    """On-demand MIT-SHM capture with native OpenCV downscaling."""

    def __init__(self, display_name: str, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("X11 desktop dimensions must be positive")
        x11_name = ctypes.util.find_library("X11")
        xext_name = ctypes.util.find_library("Xext")
        if not x11_name or not xext_name:
            raise OSError("X11 shared-memory libraries are unavailable")
        self._x11 = ctypes.CDLL(x11_name)
        self._xext = ctypes.CDLL(xext_name)
        self._libc = ctypes.CDLL(None)
        self._configure_functions()
        self.width = width
        self.height = height
        self._display: int | None = None
        self._image: ctypes.POINTER(_XImage) | None = None
        self._address: int | None = None
        self._buffer: ctypes.Array[ctypes.c_ubyte] | None = None
        self._cv2: Any = None
        self._native_bgra: Any = None
        self._info = _XShmSegmentInfo()
        self._info.shmid = -1
        self._attached = False
        self._marked_for_removal = False
        try:
            self._open(display_name)
        except Exception:
            self.close()
            raise

    def _configure_functions(self) -> None:
        self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self._x11.XOpenDisplay.restype = ctypes.c_void_p
        self._x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        self._x11.XDefaultScreen.restype = ctypes.c_int
        self._x11.XDefaultVisual.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._x11.XDefaultVisual.restype = ctypes.c_void_p
        self._x11.XDefaultDepth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._x11.XDefaultDepth.restype = ctypes.c_int
        self._x11.XRootWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._x11.XRootWindow.restype = ctypes.c_ulong
        self._x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._x11.XSync.restype = ctypes.c_int
        self._x11.XDestroyImage.argtypes = [ctypes.POINTER(_XImage)]
        self._x11.XDestroyImage.restype = ctypes.c_int
        self._x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self._x11.XCloseDisplay.restype = ctypes.c_int

        self._xext.XShmQueryExtension.argtypes = [ctypes.c_void_p]
        self._xext.XShmQueryExtension.restype = ctypes.c_int
        self._xext.XShmCreateImage.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.POINTER(_XShmSegmentInfo),
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self._xext.XShmCreateImage.restype = ctypes.POINTER(_XImage)
        self._xext.XShmAttach.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_XShmSegmentInfo),
        ]
        self._xext.XShmAttach.restype = ctypes.c_int
        self._xext.XShmDetach.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_XShmSegmentInfo),
        ]
        self._xext.XShmDetach.restype = ctypes.c_int
        self._xext.XShmGetImage.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_XImage),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self._xext.XShmGetImage.restype = ctypes.c_int

        self._libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
        self._libc.shmget.restype = ctypes.c_int
        self._libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self._libc.shmat.restype = ctypes.c_void_p
        self._libc.shmdt.argtypes = [ctypes.c_void_p]
        self._libc.shmdt.restype = ctypes.c_int
        self._libc.shmctl.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self._libc.shmctl.restype = ctypes.c_int

    def _open(self, display_name: str) -> None:
        display = self._x11.XOpenDisplay(display_name.encode())
        if not display:
            raise OSError(f"cannot open X11 display {display_name!r} for shared capture")
        self._display = display
        if not self._xext.XShmQueryExtension(display):
            raise OSError("the X11 MIT-SHM extension is unavailable")
        screen = self._x11.XDefaultScreen(display)
        visual = self._x11.XDefaultVisual(display, screen)
        depth = self._x11.XDefaultDepth(display, screen)
        self._root = self._x11.XRootWindow(display, screen)
        image = self._xext.XShmCreateImage(
            display,
            visual,
            depth,
            ZPIXMAP,
            None,
            ctypes.byref(self._info),
            self.width,
            self.height,
        )
        if not image:
            raise OSError("XShmCreateImage failed")
        self._image = image
        if (
            image.contents.bits_per_pixel != 32
            or image.contents.red_mask != 0xFF0000
            or image.contents.green_mask != 0x00FF00
            or image.contents.blue_mask != 0x0000FF
        ):
            raise OSError("unsupported X11 pixel layout for accelerated capture")
        size = image.contents.bytes_per_line * self.height
        shmid = self._libc.shmget(IPC_PRIVATE, size, IPC_CREAT | 0o600)
        if shmid < 0:
            raise OSError("shmget failed for X11 capture")
        self._info.shmid = shmid
        address = self._libc.shmat(shmid, None, 0)
        if address == ctypes.c_void_p(-1).value:
            raise OSError("shmat failed for X11 capture")
        self._address = address
        self._info.shmaddr = address
        self._info.read_only = 0
        image.contents.data = address
        if not self._xext.XShmAttach(display, ctypes.byref(self._info)):
            raise OSError("XShmAttach failed")
        self._attached = True
        self._x11.XSync(display, 0)
        if self._libc.shmctl(shmid, IPC_RMID, None) == 0:
            self._marked_for_removal = True
        self._buffer = (ctypes.c_ubyte * size).from_address(address)
        try:
            import cv2
            import numpy

            cv2.setUseOptimized(True)
            cv2.setNumThreads(min(4, os.cpu_count() or 1))
            native = numpy.ctypeslib.as_array(self._buffer).reshape(
                self.height, image.contents.bytes_per_line
            )
            self._native_bgra = native[:, : self.width * 4].reshape(
                self.height, self.width, 4
            )
            self._cv2 = cv2
        except ImportError:
            self._cv2 = None
            self._native_bgra = None

    def capture(self, target: tuple[int, int]) -> XShmFrame:
        if self._display is None or self._image is None or self._buffer is None:
            raise OSError("X11 shared capture is closed")
        if not self._xext.XShmGetImage(
            self._display,
            self._root,
            self._image,
            0,
            0,
            ALL_PLANES,
        ):
            raise OSError("XShmGetImage failed")
        captured_ns = time.monotonic_ns()
        width, height = target
        if not (1 <= width <= self.width and 1 <= height <= self.height):
            raise ValueError("X11 shared capture target is out of bounds")
        if self._cv2 is not None and self._native_bgra is not None:
            cv2 = self._cv2
            bgra = self._native_bgra
            if target != (self.width, self.height):
                bgra = cv2.resize(bgra, target, interpolation=cv2.INTER_CUBIC)
            rgb = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGB)
            image = Image.fromarray(rgb)
        else:
            image = Image.frombytes(
                "RGB",
                (self.width, self.height),
                self._buffer,
                "raw",
                "BGRX",
                self._image.contents.bytes_per_line,
                1,
            )
            if image.size != target:
                image = image.resize(target, Image.Resampling.LANCZOS)
        return XShmFrame(image, captured_ns)

    def close(self) -> None:
        display = self._display
        image = self._image
        address = self._address
        if display is not None and self._attached:
            self._xext.XShmDetach(display, ctypes.byref(self._info))
            self._x11.XSync(display, 0)
            self._attached = False
        if image is not None:
            image.contents.data = None
            self._x11.XDestroyImage(image)
            self._image = None
        self._native_bgra = None
        self._cv2 = None
        self._buffer = None
        if address is not None:
            self._libc.shmdt(address)
            self._address = None
        if self._info.shmid >= 0 and not self._marked_for_removal:
            self._libc.shmctl(self._info.shmid, IPC_RMID, None)
        self._info.shmid = -1
        if display is not None:
            self._x11.XCloseDisplay(display)
            self._display = None
