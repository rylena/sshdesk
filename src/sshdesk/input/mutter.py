from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import InputBackend
from .events import KeyCode, KeyEvent, Modifiers

SPECIAL_KEYSYMS = {
    KeyCode.ESCAPE: 0xFF1B,
    KeyCode.BACKSPACE: 0xFF08,
    KeyCode.TAB: 0xFF09,
    KeyCode.ENTER: 0xFF0D,
    KeyCode.HOME: 0xFF50,
    KeyCode.LEFT: 0xFF51,
    KeyCode.UP: 0xFF52,
    KeyCode.RIGHT: 0xFF53,
    KeyCode.DOWN: 0xFF54,
    KeyCode.PAGE_UP: 0xFF55,
    KeyCode.PAGE_DOWN: 0xFF56,
    KeyCode.END: 0xFF57,
    KeyCode.INSERT: 0xFF63,
    KeyCode.DELETE: 0xFFFF,
    KeyCode.F1: 0xFFBE,
    KeyCode.F2: 0xFFBF,
    KeyCode.F3: 0xFFC0,
    KeyCode.F4: 0xFFC1,
    KeyCode.F5: 0xFFC2,
    KeyCode.F6: 0xFFC3,
    KeyCode.F7: 0xFFC4,
    KeyCode.F8: 0xFFC5,
    KeyCode.F9: 0xFFC6,
    KeyCode.F10: 0xFFC7,
    KeyCode.F11: 0xFFC8,
    KeyCode.F12: 0xFFC9,
}
MODIFIER_KEYCODES = {
    int(Modifiers.CTRL): 29,
    int(Modifiers.SHIFT): 42,
    int(Modifiers.ALT): 56,
}
BUTTON_CODES = {1: 0x110, 2: 0x112, 3: 0x111}


class MutterInput(InputBackend):
    """Inject input directly into a linked GNOME RemoteDesktop session."""

    NAME = "org.gnome.Mutter.RemoteDesktop"
    INTERFACE = "org.gnome.Mutter.RemoteDesktop.Session"

    def __init__(
        self,
        bus: Any,
        gio: Any,
        glib: Any,
        session_path: str,
        stream_path: str,
        desktop_size: Callable[[], tuple[int, int]],
        cursor_moved: Callable[[int, int], None] | None = None,
    ) -> None:
        self._bus = bus
        self._gio = gio
        self._glib = glib
        self._session_path = session_path
        self._stream_path = stream_path
        self._desktop_size = desktop_size
        self._cursor_moved = cursor_moved
        self._pressed_keysyms: set[int] = set()
        self._pressed_modifiers: set[int] = set()
        self._pressed_buttons: set[int] = set()
        self._closed = False

    def _call(self, method: str, signature: str, values: tuple[Any, ...]) -> None:
        if self._closed:
            return
        try:
            self._bus.call_sync(
                self.NAME,
                self._session_path,
                self.INTERFACE,
                method,
                self._glib.Variant(signature, values),
                None,
                self._gio.DBusCallFlags.NONE,
                1000,
                None,
            )
        except self._glib.Error as exc:
            detail = str(exc).split(": ", 1)[-1]
            raise RuntimeError(f"GNOME input failed: {detail}") from exc

    @staticmethod
    def _keysym(event: KeyEvent) -> int | None:
        try:
            key = KeyCode(event.key_code)
        except ValueError:
            return None
        if key != KeyCode.CHARACTER:
            return SPECIAL_KEYSYMS.get(key)
        if not 0 < event.unicode <= 0x10FFFF:
            return None
        # X11 keysyms use Latin-1 directly and encode other Unicode codepoints
        # in the 0x01000000 namespace.
        if event.unicode <= 0xFF:
            return event.unicode
        return 0x01000000 | event.unicode

    @staticmethod
    def _modifier_codes(modifiers: int) -> list[int]:
        return [code for flag, code in MODIFIER_KEYCODES.items() if modifiers & flag]

    def _keycode(self, code: int, pressed: bool) -> None:
        self._call("NotifyKeyboardKeycode", "(ub)", (code, pressed))

    def _keysym_event(self, keysym: int, pressed: bool) -> None:
        self._call("NotifyKeyboardKeysym", "(ub)", (keysym, pressed))

    def key(self, event: KeyEvent) -> None:
        if event.action not in (0, 1, 2):
            return
        keysym = self._keysym(event)
        if keysym is None:
            return
        modifiers = self._modifier_codes(event.modifiers)
        if event.action in (0, 2):
            for code in modifiers:
                self._keycode(code, True)
                self._pressed_modifiers.add(code)
            self._keysym_event(keysym, True)
            self._pressed_keysyms.add(keysym)
        if event.action in (1, 2):
            self._keysym_event(keysym, False)
            self._pressed_keysyms.discard(keysym)
            for code in reversed(modifiers):
                self._keycode(code, False)
                self._pressed_modifiers.discard(code)

    def _bounded_point(self, x: int, y: int) -> tuple[float, float]:
        width, height = self._desktop_size()
        return (
            float(max(0, min(int(x), max(0, width - 1)))),
            float(max(0, min(int(y), max(0, height - 1)))),
        )

    def move(self, x: int, y: int) -> None:
        bounded_x, bounded_y = self._bounded_point(x, y)
        self._call(
            "NotifyPointerMotionAbsolute",
            "(sdd)",
            (self._stream_path, bounded_x, bounded_y),
        )
        if self._cursor_moved is not None:
            self._cursor_moved(int(bounded_x), int(bounded_y))

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        code = BUTTON_CODES.get(button)
        if code is None:
            return
        self.move(x, y)
        self._call("NotifyPointerButton", "(ib)", (code, bool(pressed)))
        if pressed:
            self._pressed_buttons.add(code)
        else:
            self._pressed_buttons.discard(code)

    def scroll(self, amount: int, x: int, y: int) -> None:
        steps = max(-20, min(20, int(amount)))
        if steps == 0:
            return
        self.move(x, y)
        self._call("NotifyPointerAxisDiscrete", "(ui)", (0, steps))

    def close(self) -> None:
        if self._closed:
            return
        for code in tuple(self._pressed_buttons):
            try:
                self._call("NotifyPointerButton", "(ib)", (code, False))
            except RuntimeError:
                pass
        for keysym in tuple(self._pressed_keysyms):
            try:
                self._keysym_event(keysym, False)
            except RuntimeError:
                pass
        for code in tuple(self._pressed_modifiers):
            try:
                self._keycode(code, False)
            except RuntimeError:
                pass
        self._pressed_buttons.clear()
        self._pressed_keysyms.clear()
        self._pressed_modifiers.clear()
        self._closed = True
