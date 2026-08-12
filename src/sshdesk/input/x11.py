from __future__ import annotations

import os

from Xlib import XK, X, display
from Xlib.ext import xtest

from .base import InputBackend
from .events import KeyCode, KeyEvent, Modifiers

SPECIAL_KEYSYMS = {
    KeyCode.ENTER: "Return",
    KeyCode.ESCAPE: "Escape",
    KeyCode.BACKSPACE: "BackSpace",
    KeyCode.TAB: "Tab",
    KeyCode.UP: "Up",
    KeyCode.DOWN: "Down",
    KeyCode.RIGHT: "Right",
    KeyCode.LEFT: "Left",
    KeyCode.HOME: "Home",
    KeyCode.END: "End",
    KeyCode.PAGE_UP: "Prior",
    KeyCode.PAGE_DOWN: "Next",
    KeyCode.INSERT: "Insert",
    KeyCode.DELETE: "Delete",
    KeyCode.F1: "F1",
    KeyCode.F2: "F2",
    KeyCode.F3: "F3",
    KeyCode.F4: "F4",
    KeyCode.F5: "F5",
    KeyCode.F6: "F6",
    KeyCode.F7: "F7",
    KeyCode.F8: "F8",
    KeyCode.F9: "F9",
    KeyCode.F10: "F10",
    KeyCode.F11: "F11",
    KeyCode.F12: "F12",
}


class X11Input(InputBackend):
    """Inject bounded keyboard and pointer events through the XTest extension."""

    def __init__(self, display_name: str | None = None) -> None:
        name = display_name or os.environ.get("DISPLAY")
        if not name:
            raise RuntimeError("DISPLAY is not set; X11 input is unavailable")
        self._display = display.Display(name)
        self._root = self._display.screen().root
        geometry = self._root.get_geometry()
        self.width, self.height = geometry.width, geometry.height
        self._pressed_keys: set[int] = set()
        self._pressed_buttons: set[int] = set()
        self._modifier_cache: dict[int, tuple[int, ...]] = {}
        self._character_cache: dict[str, tuple[int, bool]] = {}
        if not self._display.has_extension("XTEST"):
            self._display.close()
            raise RuntimeError("the X11 XTEST extension is required for input injection")

    def _modifier_keycodes(self, modifiers: int) -> tuple[int, ...]:
        cached = self._modifier_cache.get(modifiers)
        if cached is not None:
            return cached
        names = []
        if modifiers & int(Modifiers.CTRL):
            names.append("Control_L")
        if modifiers & int(Modifiers.ALT):
            names.append("Alt_L")
        if modifiers & int(Modifiers.SHIFT):
            names.append("Shift_L")
        result = tuple(
            self._display.keysym_to_keycode(XK.string_to_keysym(name)) for name in names
        )
        self._modifier_cache[modifiers] = result
        return result

    def _character_keycode(self, character: str) -> tuple[int, bool]:
        cached = self._character_cache.get(character)
        if cached is not None:
            return cached
        keysym = XK.string_to_keysym(character)
        if keysym == 0 and len(character) == 1:
            keysym = ord(character)
        for keycode in range(self._display.display.info.min_keycode, self._display.display.info.max_keycode + 1):
            for level in range(4):
                if self._display.keycode_to_keysym(keycode, level) == keysym:
                    result = keycode, level in (1, 3)
                    self._character_cache[character] = result
                    return result
        result = 0, False
        self._character_cache[character] = result
        return result

    def key(self, event: KeyEvent) -> None:
        if event.action not in (0, 1, 2):
            return
        try:
            code = KeyCode(event.key_code)
        except ValueError:
            return
        extra_shift = False
        if code == KeyCode.CHARACTER:
            if not event.unicode or event.unicode > 0x10FFFF:
                return
            keycode, extra_shift = self._character_keycode(chr(event.unicode))
        else:
            name = SPECIAL_KEYSYMS.get(code)
            keycode = self._display.keysym_to_keycode(XK.string_to_keysym(name)) if name else 0
        if not keycode:
            return
        modifiers = event.modifiers | (int(Modifiers.SHIFT) if extra_shift else 0)
        modifier_codes = self._modifier_keycodes(modifiers)
        if event.action in (0, 2):
            for modifier in modifier_codes:
                xtest.fake_input(self._display, X.KeyPress, modifier)
            xtest.fake_input(self._display, X.KeyPress, keycode)
            if event.action == 0:
                self._pressed_keys.update((*modifier_codes, keycode))
        if event.action in (1, 2):
            xtest.fake_input(self._display, X.KeyRelease, keycode)
            for modifier in reversed(modifier_codes):
                xtest.fake_input(self._display, X.KeyRelease, modifier)
            self._pressed_keys.discard(keycode)
            for modifier in modifier_codes:
                self._pressed_keys.discard(modifier)
        # X11 preserves request order; flushing avoids a server round-trip for
        # every event while still delivering input immediately.
        self._display.flush()

    def _bounded(self, x: int, y: int) -> tuple[int, int]:
        return min(max(0, x), self.width - 1), min(max(0, y), self.height - 1)

    def move(self, x: int, y: int) -> None:
        x, y = self._bounded(x, y)
        xtest.fake_input(self._display, X.MotionNotify, x=x, y=y)
        self._display.flush()

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        if button not in (1, 2, 3):
            return
        x, y = self._bounded(x, y)
        xtest.fake_input(self._display, X.MotionNotify, x=x, y=y)
        xtest.fake_input(self._display, X.ButtonPress if pressed else X.ButtonRelease, button)
        if pressed:
            self._pressed_buttons.add(button)
        else:
            self._pressed_buttons.discard(button)
        self._display.flush()

    def scroll(self, amount: int, x: int, y: int) -> None:
        x, y = self._bounded(x, y)
        xtest.fake_input(self._display, X.MotionNotify, x=x, y=y)
        button = 4 if amount > 0 else 5
        for _ in range(min(abs(amount), 20)):
            xtest.fake_input(self._display, X.ButtonPress, button)
            xtest.fake_input(self._display, X.ButtonRelease, button)
        self._display.flush()

    def close(self) -> None:
        if self._display is not None:
            for button in tuple(self._pressed_buttons):
                xtest.fake_input(self._display, X.ButtonRelease, button)
            for keycode in tuple(self._pressed_keys):
                xtest.fake_input(self._display, X.KeyRelease, keycode)
            if self._pressed_buttons or self._pressed_keys:
                self._display.sync()
            self._pressed_buttons.clear()
            self._pressed_keys.clear()
            self._display.close()
            self._display = None
