from __future__ import annotations

from typing import Any

from .base import InputBackend
from .events import KeyCode, KeyEvent, Modifiers

MAC_KEYCODES = {
    KeyCode.ENTER: 36,
    KeyCode.TAB: 48,
    KeyCode.BACKSPACE: 51,
    KeyCode.ESCAPE: 53,
    KeyCode.HOME: 115,
    KeyCode.END: 119,
    KeyCode.PAGE_UP: 116,
    KeyCode.PAGE_DOWN: 121,
    KeyCode.DELETE: 117,
    KeyCode.LEFT: 123,
    KeyCode.RIGHT: 124,
    KeyCode.DOWN: 125,
    KeyCode.UP: 126,
    KeyCode.F1: 122,
    KeyCode.F2: 120,
    KeyCode.F3: 99,
    KeyCode.F4: 118,
    KeyCode.F5: 96,
    KeyCode.F6: 97,
    KeyCode.F7: 98,
    KeyCode.F8: 100,
    KeyCode.F9: 101,
    KeyCode.F10: 109,
    KeyCode.F11: 103,
    KeyCode.F12: 111,
}


class MacOSInput(InputBackend):
    """macOS Quartz input injection with Accessibility permission checks."""

    def __init__(self) -> None:
        try:
            import Quartz
        except ImportError as exc:
            raise RuntimeError(
                "macOS input needs the macOS extra: pip install 'sshdesk[macos]'"
            ) from exc
        self.q: Any = Quartz
        if not Quartz.AXIsProcessTrusted():
            raise RuntimeError(
                "grant Accessibility permission to the SSH/Python process for input control"
            )
        display = Quartz.CGMainDisplayID()
        self.width = int(Quartz.CGDisplayPixelsWide(display))
        self.height = int(Quartz.CGDisplayPixelsHigh(display))
        self.position = (0.0, 0.0)
        self._pressed_buttons: set[int] = set()
        self._pressed_keys: set[int] = set()

    def _post(self, event: object) -> None:
        self.q.CGEventPost(self.q.kCGHIDEventTap, event)

    def _flags(self, modifiers: int) -> int:
        flags = 0
        if modifiers & int(Modifiers.CTRL):
            flags |= self.q.kCGEventFlagMaskControl
        if modifiers & int(Modifiers.ALT):
            flags |= self.q.kCGEventFlagMaskAlternate
        if modifiers & int(Modifiers.SHIFT):
            flags |= self.q.kCGEventFlagMaskShift
        return flags

    def key(self, event: KeyEvent) -> None:
        if event.action not in (0, 1, 2):
            return
        try:
            key = KeyCode(event.key_code)
        except ValueError:
            return
        code = MAC_KEYCODES.get(key, 0)
        character = chr(event.unicode) if key == KeyCode.CHARACTER and event.unicode else ""
        if key != KeyCode.CHARACTER and key not in MAC_KEYCODES:
            return
        for pressed in ((True,) if event.action == 0 else (False,) if event.action == 1 else (True, False)):
            generated = self.q.CGEventCreateKeyboardEvent(None, code, pressed)
            if character:
                self.q.CGEventKeyboardSetUnicodeString(generated, len(character), character)
            self.q.CGEventSetFlags(generated, self._flags(event.modifiers))
            self._post(generated)
        if event.action == 0:
            self._pressed_keys.add(code)
        else:
            self._pressed_keys.discard(code)

    def _point(self, x: int, y: int) -> tuple[float, float]:
        return float(min(max(0, x), self.width - 1)), float(min(max(0, y), self.height - 1))

    def move(self, x: int, y: int) -> None:
        self.position = self._point(x, y)
        kind = self.q.kCGEventMouseMoved
        if 1 in self._pressed_buttons:
            kind = self.q.kCGEventLeftMouseDragged
        elif 3 in self._pressed_buttons:
            kind = self.q.kCGEventRightMouseDragged
        event = self.q.CGEventCreateMouseEvent(None, kind, self.position, 0)
        self._post(event)

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        if button not in (1, 2, 3):
            return
        self.position = self._point(x, y)
        mouse_button = {1: 0, 2: 2, 3: 1}[button]
        down = {
            1: self.q.kCGEventLeftMouseDown,
            2: self.q.kCGEventOtherMouseDown,
            3: self.q.kCGEventRightMouseDown,
        }
        up = {
            1: self.q.kCGEventLeftMouseUp,
            2: self.q.kCGEventOtherMouseUp,
            3: self.q.kCGEventRightMouseUp,
        }
        event = self.q.CGEventCreateMouseEvent(
            None, down[button] if pressed else up[button], self.position, mouse_button
        )
        self._post(event)
        if pressed:
            self._pressed_buttons.add(button)
        else:
            self._pressed_buttons.discard(button)

    def scroll(self, amount: int, x: int, y: int) -> None:
        self.move(x, y)
        event = self.q.CGEventCreateScrollWheelEvent(
            None, self.q.kCGScrollEventUnitLine, 1, max(-20, min(20, amount))
        )
        self._post(event)

    def close(self) -> None:
        for button in tuple(self._pressed_buttons):
            self.button(button, False, int(self.position[0]), int(self.position[1]))
        for code in tuple(self._pressed_keys):
            self._post(self.q.CGEventCreateKeyboardEvent(None, code, False))
        self._pressed_buttons.clear()
        self._pressed_keys.clear()
