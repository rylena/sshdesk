from __future__ import annotations

import shutil
import subprocess

from .base import InputBackend
from .events import KeyCode, KeyEvent, Modifiers

SPECIAL_KEYS = {
    KeyCode.ESCAPE: 1,
    KeyCode.BACKSPACE: 14,
    KeyCode.TAB: 15,
    KeyCode.ENTER: 28,
    KeyCode.F1: 59,
    KeyCode.F2: 60,
    KeyCode.F3: 61,
    KeyCode.F4: 62,
    KeyCode.F5: 63,
    KeyCode.F6: 64,
    KeyCode.F7: 65,
    KeyCode.F8: 66,
    KeyCode.F9: 67,
    KeyCode.F10: 68,
    KeyCode.F11: 87,
    KeyCode.F12: 88,
    KeyCode.HOME: 102,
    KeyCode.UP: 103,
    KeyCode.PAGE_UP: 104,
    KeyCode.LEFT: 105,
    KeyCode.RIGHT: 106,
    KeyCode.END: 107,
    KeyCode.DOWN: 108,
    KeyCode.PAGE_DOWN: 109,
    KeyCode.INSERT: 110,
    KeyCode.DELETE: 111,
}
LETTER_KEYS = {letter: 30 + index for index, letter in enumerate("asdfghjkl")}
LETTER_KEYS.update({letter: 16 + index for index, letter in enumerate("qwertyuiop")})
LETTER_KEYS.update({letter: 44 + index for index, letter in enumerate("zxcvbnm")})
MODIFIER_KEYS = {
    int(Modifiers.CTRL): 29,
    int(Modifiers.SHIFT): 42,
    int(Modifiers.ALT): 56,
}


class YdotoolInput(InputBackend):
    """Compositor-independent Linux input through ydotoold and /dev/uinput."""

    def __init__(self) -> None:
        executable = shutil.which("ydotool")
        if executable is None:
            raise RuntimeError(
                "Wayland input needs ydotool and a running ydotoold with /dev/uinput access"
            )
        self.executable = executable
        self._pressed_buttons: set[int] = set()
        self._pressed_keys: set[int] = set()
        # `debug` only asks the daemon for its device information; it does not
        # inject an event. Checking here makes `sshdesk-server --check` verify
        # the socket and its permissions, not merely the client executable.
        self._run("debug")

    def _run(self, *arguments: str) -> None:
        result = subprocess.run(
            [self.executable, *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=2.0,
        )
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"ydotool input failed: {detail or 'is ydotoold running?'}")

    @staticmethod
    def _modifier_codes(modifiers: int) -> list[int]:
        return [code for flag, code in MODIFIER_KEYS.items() if modifiers & flag]

    def key(self, event: KeyEvent) -> None:
        if event.action not in (0, 1, 2):
            return
        try:
            key = KeyCode(event.key_code)
        except ValueError:
            return
        character = ""
        code = SPECIAL_KEYS.get(key)
        if key == KeyCode.CHARACTER and 0 < event.unicode <= 0x10FFFF:
            character = chr(event.unicode)
            code = LETTER_KEYS.get(character.lower())

        modifier_bits = event.modifiers
        if character.isupper():
            modifier_bits |= int(Modifiers.SHIFT)
        modifiers = self._modifier_codes(modifier_bits)
        if code is None and character and event.action == 2 and not modifiers:
            self._run("type", "--key-delay", "0", "--", character)
            return
        if code is None:
            return

        sequence: list[str] = []
        if event.action in (0, 2):
            sequence.extend(f"{modifier}:1" for modifier in modifiers)
            sequence.append(f"{code}:1")
            if event.action == 0:
                self._pressed_keys.update((*modifiers, code))
        if event.action in (1, 2):
            sequence.append(f"{code}:0")
            sequence.extend(f"{modifier}:0" for modifier in reversed(modifiers))
            self._pressed_keys.discard(code)
            for modifier in modifiers:
                self._pressed_keys.discard(modifier)
        self._run("key", "--key-delay", "0", *sequence)

    def move(self, x: int, y: int) -> None:
        self._run("mousemove", "--absolute", str(max(0, x)), str(max(0, y)))

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        if button not in (1, 2, 3):
            return
        self.move(x, y)
        base = {1: 0, 2: 2, 3: 1}[button]
        mask = 0x40 if pressed else 0x80
        self._run("click", "--next-delay", "0", hex(mask | base))
        if pressed:
            self._pressed_buttons.add(button)
        else:
            self._pressed_buttons.discard(button)

    def scroll(self, amount: int, x: int, y: int) -> None:
        self.move(x, y)
        self._run("mousemove", "--wheel", "0", str(max(-20, min(20, amount))))

    def close(self) -> None:
        for button in tuple(self._pressed_buttons):
            base = {1: 0, 2: 2, 3: 1}[button]
            try:
                self._run("click", "--next-delay", "0", hex(0x80 | base))
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
        if self._pressed_keys:
            try:
                self._run(
                    "key",
                    "--key-delay",
                    "0",
                    *(f"{code}:0" for code in self._pressed_keys),
                )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
        self._pressed_buttons.clear()
        self._pressed_keys.clear()
