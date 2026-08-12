from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import ClassVar

from .base import InputBackend
from .events import KeyCode, KeyEvent, Modifiers

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120

VK_KEYS = {
    KeyCode.BACKSPACE: 0x08,
    KeyCode.TAB: 0x09,
    KeyCode.ENTER: 0x0D,
    KeyCode.ESCAPE: 0x1B,
    KeyCode.PAGE_UP: 0x21,
    KeyCode.PAGE_DOWN: 0x22,
    KeyCode.END: 0x23,
    KeyCode.HOME: 0x24,
    KeyCode.LEFT: 0x25,
    KeyCode.UP: 0x26,
    KeyCode.RIGHT: 0x27,
    KeyCode.DOWN: 0x28,
    KeyCode.INSERT: 0x2D,
    KeyCode.DELETE: 0x2E,
    **{KeyCode(20 + index): 0x70 + index for index in range(12)},
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_: ClassVar[list[tuple[str, object]]] = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _INPUTUNION)]


class WindowsInput(InputBackend):
    """Windows SendInput backend for the interactive desktop session."""

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.left = self.user32.GetSystemMetrics(76)
        self.top = self.user32.GetSystemMetrics(77)
        self.width = max(1, self.user32.GetSystemMetrics(78))
        self.height = max(1, self.user32.GetSystemMetrics(79))
        self._pressed_buttons: set[int] = set()
        self._pressed_keys: set[int] = set()

    def _send(self, value: _INPUT) -> None:
        if self.user32.SendInput(1, ctypes.byref(value), ctypes.sizeof(_INPUT)) != 1:
            raise OSError(ctypes.get_last_error(), "Windows SendInput failed")

    def _keyboard(self, vk: int, scan: int, flags: int) -> None:
        self._send(_INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(vk, scan, flags, 0, None)))

    def key(self, event: KeyEvent) -> None:
        if event.action not in (0, 1, 2):
            return
        try:
            key = KeyCode(event.key_code)
        except ValueError:
            return
        modifiers = []
        if event.modifiers & int(Modifiers.CTRL):
            modifiers.append(0x11)
        if event.modifiers & int(Modifiers.ALT):
            modifiers.append(0x12)
        if event.modifiers & int(Modifiers.SHIFT):
            modifiers.append(0x10)
        if event.action in (0, 2):
            for modifier in modifiers:
                self._keyboard(modifier, 0, 0)
                if event.action == 0:
                    self._pressed_keys.add(modifier)
        flags = 0 if event.action == 0 else KEYEVENTF_KEYUP if event.action == 1 else 0
        if key == KeyCode.CHARACTER and 0 < event.unicode <= 0x10FFFF:
            encoded = chr(event.unicode).encode("utf-16-le")
            units = [int.from_bytes(encoded[index : index + 2], "little") for index in range(0, len(encoded), 2)]
            for unit in units:
                self._keyboard(0, unit, KEYEVENTF_UNICODE | flags)
                if event.action == 2:
                    self._keyboard(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        else:
            vk = VK_KEYS.get(key)
            if vk is None:
                if event.action in (1, 2):
                    for modifier in reversed(modifiers):
                        self._keyboard(modifier, 0, KEYEVENTF_KEYUP)
                return
            self._keyboard(vk, 0, flags)
            if event.action == 2:
                self._keyboard(vk, 0, KEYEVENTF_KEYUP)
            if event.action == 0:
                self._pressed_keys.add(vk)
            else:
                self._pressed_keys.discard(vk)
        if event.action in (1, 2):
            for modifier in reversed(modifiers):
                self._keyboard(modifier, 0, KEYEVENTF_KEYUP)
                self._pressed_keys.discard(modifier)

    def _mouse(self, x: int, y: int, flags: int, data: int = 0) -> None:
        absolute_x = round(min(max(0, x), self.width - 1) * 65535 / max(1, self.width - 1))
        absolute_y = round(min(max(0, y), self.height - 1) * 65535 / max(1, self.height - 1))
        flags |= MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        self._send(_INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(absolute_x, absolute_y, data, flags, 0, None)))

    def move(self, x: int, y: int) -> None:
        self._mouse(x, y, MOUSEEVENTF_MOVE)

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        if button not in (1, 2, 3):
            return
        flags = {
            (1, True): MOUSEEVENTF_LEFTDOWN,
            (1, False): MOUSEEVENTF_LEFTUP,
            (2, True): MOUSEEVENTF_MIDDLEDOWN,
            (2, False): MOUSEEVENTF_MIDDLEUP,
            (3, True): MOUSEEVENTF_RIGHTDOWN,
            (3, False): MOUSEEVENTF_RIGHTUP,
        }[button, pressed]
        self._mouse(x, y, MOUSEEVENTF_MOVE | flags)
        if pressed:
            self._pressed_buttons.add(button)
        else:
            self._pressed_buttons.discard(button)

    def scroll(self, amount: int, x: int, y: int) -> None:
        data = ctypes.c_ulong(max(-20, min(20, amount)) * WHEEL_DELTA).value
        self._mouse(x, y, MOUSEEVENTF_MOVE | MOUSEEVENTF_WHEEL, data)

    def close(self) -> None:
        for button in tuple(self._pressed_buttons):
            self.button(button, False, 0, 0)
        for vk in tuple(self._pressed_keys):
            self._keyboard(vk, 0, KEYEVENTF_KEYUP)
        self._pressed_buttons.clear()
        self._pressed_keys.clear()
