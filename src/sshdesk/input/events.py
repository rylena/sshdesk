from __future__ import annotations

import enum
from dataclasses import dataclass


class Modifiers(enum.IntFlag):
    NONE = 0
    SHIFT = 1
    ALT = 2
    CTRL = 4


class KeyCode(enum.IntEnum):
    CHARACTER = 0
    ENTER = 1
    ESCAPE = 2
    BACKSPACE = 3
    TAB = 4
    UP = 5
    DOWN = 6
    RIGHT = 7
    LEFT = 8
    HOME = 9
    END = 10
    PAGE_UP = 11
    PAGE_DOWN = 12
    INSERT = 13
    DELETE = 14
    F1 = 20
    F2 = 21
    F3 = 22
    F4 = 23
    F5 = 24
    F6 = 25
    F7 = 26
    F8 = 27
    F9 = 28
    F10 = 29
    F11 = 30
    F12 = 31


class ControlKind(enum.Enum):
    EXIT = "exit"
    TOGGLE_STATS = "toggle_stats"


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """A normalized key action independent of terminal and desktop backends.

    action is 0 for press, 1 for release, and 2 for a complete tap.
    """

    action: int
    modifiers: int
    key_code: int
    unicode: int = 0


@dataclass(frozen=True, slots=True)
class ControlEvent:
    kind: ControlKind


@dataclass(frozen=True, slots=True)
class MouseMoveEvent:
    column: int
    row: int


@dataclass(frozen=True, slots=True)
class MouseButtonEvent:
    button: int
    pressed: bool
    column: int
    row: int


@dataclass(frozen=True, slots=True)
class MouseScrollEvent:
    amount: int
    column: int
    row: int


@dataclass(frozen=True, slots=True)
class TerminalReportEvent:
    """Response to an ANSI cursor-position query used for terminal RTT."""

    column: int
    row: int


TerminalInputEvent = (
    KeyEvent
    | ControlEvent
    | MouseMoveEvent
    | MouseButtonEvent
    | MouseScrollEvent
    | TerminalReportEvent
)
