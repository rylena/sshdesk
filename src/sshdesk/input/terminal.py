from __future__ import annotations

import re
import time

from sshdesk.render.base import Viewport

from .events import (
    ControlEvent,
    ControlKind,
    KeyCode,
    KeyEvent,
    Modifiers,
    MouseButtonEvent,
    MouseMoveEvent,
    MouseScrollEvent,
    TerminalInputEvent,
    TerminalReportEvent,
)

MOUSE_RE = re.compile(rb"^\x1b\[<(\d{1,5});(\d{1,5});(\d{1,5})([Mm])")
CSI_KEY_RE = re.compile(rb"^\x1b\[(?:(\d+)(?:;(\d+))?)?([A-DFHZ])")
CSI_TILDE_RE = re.compile(rb"^\x1b\[(\d+)(?:;(\d+))?~")
LEGACY_MOUSE_PREFIX = b"\x1b[M"
CURSOR_REPORT_RE = re.compile(rb"^\x1b\[(\d{1,4});(\d{1,4})R")
CSI_KEYS = {
    b"A": KeyCode.UP,
    b"B": KeyCode.DOWN,
    b"C": KeyCode.RIGHT,
    b"D": KeyCode.LEFT,
    b"H": KeyCode.HOME,
    b"F": KeyCode.END,
    b"Z": KeyCode.TAB,
}
TILDE_KEYS = {
    1: KeyCode.HOME,
    2: KeyCode.INSERT,
    3: KeyCode.DELETE,
    4: KeyCode.END,
    5: KeyCode.PAGE_UP,
    6: KeyCode.PAGE_DOWN,
    15: KeyCode.F5,
    17: KeyCode.F6,
    18: KeyCode.F7,
    19: KeyCode.F8,
    20: KeyCode.F9,
    21: KeyCode.F10,
    23: KeyCode.F11,
    24: KeyCode.F12,
}
SEQUENCES = {
    b"\x1b[A": KeyCode.UP,
    b"\x1b[B": KeyCode.DOWN,
    b"\x1b[C": KeyCode.RIGHT,
    b"\x1b[D": KeyCode.LEFT,
    b"\x1b[H": KeyCode.HOME,
    b"\x1b[F": KeyCode.END,
    b"\x1bOH": KeyCode.HOME,
    b"\x1bOF": KeyCode.END,
    b"\x1b[2~": KeyCode.INSERT,
    b"\x1b[3~": KeyCode.DELETE,
    b"\x1b[5~": KeyCode.PAGE_UP,
    b"\x1b[6~": KeyCode.PAGE_DOWN,
    b"\x1bOP": KeyCode.F1,
    b"\x1bOQ": KeyCode.F2,
    b"\x1bOR": KeyCode.F3,
    b"\x1bOS": KeyCode.F4,
    b"\x1b[15~": KeyCode.F5,
    b"\x1b[17~": KeyCode.F6,
    b"\x1b[18~": KeyCode.F7,
    b"\x1b[19~": KeyCode.F8,
    b"\x1b[20~": KeyCode.F9,
    b"\x1b[21~": KeyCode.F10,
    b"\x1b[23~": KeyCode.F11,
    b"\x1b[24~": KeyCode.F12,
}


def translate_coordinates(column: int, row: int, viewport: Viewport) -> tuple[int, int] | None:
    """Map zero-based terminal cells into remote desktop coordinates."""
    if not (
        viewport.x <= column < viewport.x + viewport.width
        and viewport.y <= row < viewport.y + viewport.height
    ):
        return None
    local_x = column - viewport.x
    local_y = row - viewport.y
    remote_x = min(
        viewport.desktop_width - 1,
        int((local_x + 0.5) * viewport.desktop_width / viewport.width),
    )
    remote_y = min(
        viewport.desktop_height - 1,
        int((local_y + 0.5) * viewport.desktop_height / viewport.height),
    )
    return remote_x, remote_y


class TerminalEventParser:
    """Incremental parser for UTF-8 keys and SGR terminal mouse reporting."""

    ESCAPE_DELAY = 0.035

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.detach_pending = False
        self.escape_since: float | None = None
        self.legacy_button: int | None = None

    @staticmethod
    def _tap(code: KeyCode, modifiers: Modifiers = Modifiers.NONE, unicode: int = 0) -> KeyEvent:
        return KeyEvent(2, int(modifiers), int(code), unicode)

    @staticmethod
    def _utf8_length(first: int) -> int:
        if first < 0x80:
            return 1
        if 0xC2 <= first <= 0xDF:
            return 2
        if 0xE0 <= first <= 0xEF:
            return 3
        if 0xF0 <= first <= 0xF4:
            return 4
        return 1

    def feed(self, data: bytes, now: float | None = None) -> list[TerminalInputEvent]:
        self.buffer.extend(data)
        if len(self.buffer) > 8192:
            self.buffer.clear()
            raise ValueError("terminal input sequence exceeds 8192 bytes")
        events: list[TerminalInputEvent] = []
        now = time.monotonic() if now is None else now
        while self.buffer:
            first = self.buffer[0]
            if self.detach_pending:
                self.detach_pending = False
                if first == 0x1D:
                    del self.buffer[0]
                    events.append(ControlEvent(ControlKind.EXIT))
                    continue
                events.append(self._tap(KeyCode.CHARACTER, Modifiers.CTRL, ord("]")))
            if first == 0x1D:  # Ctrl+], twice detaches locally.
                del self.buffer[0]
                self.detach_pending = True
                continue
            if first == 0x13:  # Ctrl+Shift+S is encoded as Ctrl+S by terminals.
                del self.buffer[0]
                events.append(ControlEvent(ControlKind.TOGGLE_STATS))
                continue
            if first in (10, 13):
                del self.buffer[0]
                events.append(self._tap(KeyCode.ENTER))
                continue
            if first in (8, 127):
                del self.buffer[0]
                events.append(self._tap(KeyCode.BACKSPACE))
                continue
            if first == 9:
                del self.buffer[0]
                events.append(self._tap(KeyCode.TAB))
                continue
            if 1 <= first <= 26:
                del self.buffer[0]
                events.append(
                    self._tap(KeyCode.CHARACTER, Modifiers.CTRL, ord("a") + first - 1)
                )
                continue
            if first == 0x1B:
                report_match = CURSOR_REPORT_RE.match(self.buffer)
                if report_match:
                    row, column = (max(1, int(value)) - 1 for value in report_match.groups())
                    del self.buffer[: report_match.end()]
                    events.append(TerminalReportEvent(column, row))
                    self.escape_since = None
                    continue
                match = MOUSE_RE.match(self.buffer)
                if match:
                    raw_button, raw_column, raw_row, suffix = match.groups()
                    del self.buffer[: match.end()]
                    button_code = int(raw_button)
                    column = max(0, int(raw_column) - 1)
                    row = max(0, int(raw_row) - 1)
                    if button_code & 64:
                        amount = -1 if button_code & 1 else 1
                        events.append(MouseScrollEvent(amount, column, row))
                    elif button_code & 32:
                        events.append(MouseMoveEvent(column, row))
                    else:
                        button = (button_code & 3) + 1
                        pressed = suffix == b"M"
                        if button <= 3:
                            events.append(MouseButtonEvent(button, pressed, column, row))
                    self.escape_since = None
                    continue
                if self.buffer.startswith(LEGACY_MOUSE_PREFIX):
                    if len(self.buffer) < 6:
                        break
                    button_code = self.buffer[3] - 32
                    column = max(0, self.buffer[4] - 33)
                    row = max(0, self.buffer[5] - 33)
                    del self.buffer[:6]
                    base_button = button_code & 3
                    if button_code & 64:
                        amount = -1 if button_code & 1 else 1
                        events.append(MouseScrollEvent(amount, column, row))
                    elif button_code & 32:
                        events.append(MouseMoveEvent(column, row))
                    elif base_button == 3:
                        button = self.legacy_button or 1
                        events.append(MouseButtonEvent(button, False, column, row))
                        self.legacy_button = None
                    else:
                        button = base_button + 1
                        self.legacy_button = button
                        events.append(MouseButtonEvent(button, True, column, row))
                    self.escape_since = None
                    continue
                if len(self.buffer) > 1 and LEGACY_MOUSE_PREFIX.startswith(self.buffer):
                    break
                csi_match = CSI_KEY_RE.match(self.buffer)
                if csi_match:
                    _parameter, modifier_parameter, final = csi_match.groups()
                    del self.buffer[: csi_match.end()]
                    modifiers = self._xterm_modifiers(modifier_parameter)
                    if final == b"Z":
                        modifiers |= Modifiers.SHIFT
                    events.append(self._tap(CSI_KEYS[final], modifiers))
                    self.escape_since = None
                    continue
                tilde_match = CSI_TILDE_RE.match(self.buffer)
                if tilde_match:
                    number, modifier_parameter = tilde_match.groups()
                    code = TILDE_KEYS.get(int(number))
                    if code is not None:
                        del self.buffer[: tilde_match.end()]
                        events.append(self._tap(code, self._xterm_modifiers(modifier_parameter)))
                        self.escape_since = None
                        continue
                sequence = next(
                    (
                        sequence
                        for sequence in sorted(SEQUENCES, key=len, reverse=True)
                        if self.buffer.startswith(sequence)
                    ),
                    None,
                )
                if sequence is not None:
                    del self.buffer[: len(sequence)]
                    events.append(self._tap(SEQUENCES[sequence]))
                    self.escape_since = None
                    continue
                if (
                    any(sequence.startswith(self.buffer) for sequence in SEQUENCES)
                    or b"\x1b[<".startswith(self.buffer)
                    or self.buffer.startswith(b"\x1b[<")
                ):
                    if self.escape_since is not None and now - self.escape_since >= self.ESCAPE_DELAY:
                        del self.buffer[0]
                        events.append(self._tap(KeyCode.ESCAPE))
                        self.escape_since = None
                        continue
                    if self.escape_since is None:
                        self.escape_since = now
                    break
                if self.buffer.startswith(b"\x1b[") and not any(
                    0x40 <= value <= 0x7E for value in self.buffer[2:]
                ):
                    if self.escape_since is None:
                        self.escape_since = now
                    if now - self.escape_since < self.ESCAPE_DELAY:
                        break
                if len(self.buffer) >= 2 and self.buffer[1] not in (ord("["), ord("O")):
                    del self.buffer[0]
                    length = self._utf8_length(self.buffer[0])
                    if len(self.buffer) < length:
                        self.buffer.insert(0, 0x1B)
                        break
                    encoded = bytes(self.buffer[:length])
                    try:
                        character = encoded.decode("utf-8")
                    except UnicodeDecodeError:
                        character = "�"
                        length = 1
                    del self.buffer[:length]
                    events.append(self._tap(KeyCode.CHARACTER, Modifiers.ALT, ord(character)))
                    self.escape_since = None
                    continue
                if self.escape_since is None:
                    self.escape_since = now
                if now - self.escape_since < self.ESCAPE_DELAY:
                    break
                del self.buffer[0]
                events.append(self._tap(KeyCode.ESCAPE))
                self.escape_since = None
                continue
            length = self._utf8_length(first)
            if len(self.buffer) < length:
                break
            encoded = bytes(self.buffer[:length])
            try:
                character = encoded.decode("utf-8")
            except UnicodeDecodeError:
                character = "�"
                length = 1
            del self.buffer[:length]
            modifiers = Modifiers.SHIFT if character.isalpha() and character.isupper() else Modifiers.NONE
            events.append(self._tap(KeyCode.CHARACTER, modifiers, ord(character)))
        return events

    def flush(self, now: float | None = None) -> list[TerminalInputEvent]:
        return self.feed(b"", time.monotonic() if now is None else now)

    @staticmethod
    def _xterm_modifiers(parameter: bytes | None) -> Modifiers:
        if parameter is None:
            return Modifiers.NONE
        value = max(0, int(parameter) - 1)
        modifiers = Modifiers.NONE
        if value & 1:
            modifiers |= Modifiers.SHIFT
        if value & 2:
            modifiers |= Modifiers.ALT
        if value & 4:
            modifiers |= Modifiers.CTRL
        return modifiers
