from __future__ import annotations

import unittest
from types import SimpleNamespace

from sshdesk.input.events import (
    ControlEvent,
    ControlKind,
    KeyCode,
    KeyEvent,
    Modifiers,
    MouseButtonEvent,
    MouseMoveEvent,
    MouseScrollEvent,
    TerminalReportEvent,
)
from sshdesk.input.mutter import MutterInput
from sshdesk.input.terminal import TerminalEventParser, translate_coordinates
from sshdesk.render.base import Viewport
from sshdesk.render.kitty import PixelViewport, translate_pixel_coordinates


class InputTests(unittest.TestCase):
    def test_key_mapping(self) -> None:
        parser = TerminalEventParser()
        events = parser.feed(b"aA\r\x7f\t\x1b[A\x1b[24~\x01")
        expected_codes = [
            KeyCode.CHARACTER,
            KeyCode.CHARACTER,
            KeyCode.ENTER,
            KeyCode.BACKSPACE,
            KeyCode.TAB,
            KeyCode.UP,
            KeyCode.F12,
            KeyCode.CHARACTER,
        ]
        self.assertEqual([KeyCode(event.key_code) for event in events], expected_codes)
        self.assertEqual(events[-1].modifiers, int(Modifiers.CTRL))

    def test_escape_alt_and_detach(self) -> None:
        parser = TerminalEventParser()
        alt = parser.feed(b"\x1bx")
        self.assertEqual(alt, [KeyEvent(2, int(Modifiers.ALT), KeyCode.CHARACTER, ord("x"))])
        self.assertEqual(parser.feed(b"\x1d\x1d"), [ControlEvent(ControlKind.EXIT)])

        parser = TerminalEventParser()
        self.assertEqual(parser.feed(b"\x1b", now=1.0), [])
        self.assertEqual(parser.flush(now=1.1), [KeyEvent(2, 0, KeyCode.ESCAPE, 0)])

    def test_sgr_mouse(self) -> None:
        parser = TerminalEventParser()
        events = parser.feed(
            b"\x1b[<0;10;5M\x1b[<0;10;5m\x1b[<32;11;6M\x1b[<64;11;6M\x1b[<65;11;6M"
        )
        self.assertEqual(events[0], MouseButtonEvent(1, True, 9, 4))
        self.assertEqual(events[1], MouseButtonEvent(1, False, 9, 4))
        self.assertEqual(events[2], MouseMoveEvent(10, 5))
        self.assertEqual(events[3], MouseScrollEvent(1, 10, 5))
        self.assertEqual(events[4], MouseScrollEvent(-1, 10, 5))

    def test_legacy_x10_mouse_fallback(self) -> None:
        parser = TerminalEventParser()
        press = b"\x1b[M" + bytes((32, 42, 37))
        release = b"\x1b[M" + bytes((35, 42, 37))
        self.assertEqual(
            parser.feed(press + release),
            [
                MouseButtonEvent(1, True, 9, 4),
                MouseButtonEvent(1, False, 9, 4),
            ],
        )

    def test_modified_navigation_keys(self) -> None:
        parser = TerminalEventParser()
        events = parser.feed(b"\x1b[1;5A\x1b[1;2D\x1b[3;3~\x1b[Z")
        self.assertEqual(
            [(KeyCode(event.key_code), Modifiers(event.modifiers)) for event in events],
            [
                (KeyCode.UP, Modifiers.CTRL),
                (KeyCode.LEFT, Modifiers.SHIFT),
                (KeyCode.DELETE, Modifiers.ALT),
                (KeyCode.TAB, Modifiers.SHIFT),
            ],
        )

    def test_cursor_report_is_consumed_as_latency_event(self) -> None:
        parser = TerminalEventParser()
        self.assertEqual(parser.feed(b"\x1b[12;40R"), [TerminalReportEvent(39, 11)])
        parser = TerminalEventParser()
        self.assertEqual(parser.feed(b"\x1b[12;"), [])
        self.assertEqual(parser.feed(b"40R"), [TerminalReportEvent(39, 11)])

    def test_oversized_terminal_sequence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "8192"):
            TerminalEventParser().feed(b"\x1b[<" + b"1" * 9000)

    def test_coordinate_translation_and_letterbox(self) -> None:
        viewport = Viewport(10, 5, 100, 50, 1920, 1080)
        self.assertIsNone(translate_coordinates(9, 5, viewport))
        self.assertIsNone(translate_coordinates(110, 5, viewport))
        self.assertEqual(translate_coordinates(10, 5, viewport), (9, 10))
        self.assertEqual(translate_coordinates(109, 54, viewport), (1910, 1069))

    def test_pixel_coordinate_translation_and_letterbox(self) -> None:
        viewport = PixelViewport(80, 40, 800, 450, 1920, 1080)
        self.assertIsNone(translate_pixel_coordinates(79, 40, viewport))
        self.assertEqual(translate_pixel_coordinates(80, 40, viewport), (1, 1))
        self.assertEqual(translate_pixel_coordinates(879, 489, viewport), (1918, 1078))

    def test_mutter_input_uses_linked_stream_and_bounds_pointer(self) -> None:
        calls: list[tuple[str, str, tuple[object, ...]]] = []

        class FakeBus:
            @staticmethod
            def call_sync(
                _name: str,
                _path: str,
                _interface: str,
                method: str,
                parameters: object,
                *_args: object,
            ) -> None:
                calls.append((method, parameters.signature, parameters.values))

        class Variant:
            def __init__(self, signature: str, values: tuple[object, ...]) -> None:
                self.signature = signature
                self.values = values

        backend = MutterInput(
            FakeBus(),
            SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0)),
            SimpleNamespace(Variant=Variant, Error=RuntimeError),
            "/remote/session",
            "/screen/stream",
            lambda: (1920, 1080),
        )
        backend.move(9999, -50)
        backend.key(KeyEvent(2, int(Modifiers.CTRL), int(KeyCode.CHARACTER), ord("c")))
        backend.button(1, True, 20, 30)
        backend.button(1, False, 20, 30)
        backend.close()

        self.assertEqual(
            calls[0],
            (
                "NotifyPointerMotionAbsolute",
                "(sdd)",
                ("/screen/stream", 1919.0, 0.0),
            ),
        )
        self.assertIn(("NotifyKeyboardKeycode", "(ub)", (29, True)), calls)
        self.assertIn(("NotifyKeyboardKeysym", "(ub)", (ord("c"), True)), calls)
        self.assertIn(("NotifyPointerButton", "(ib)", (0x110, True)), calls)


if __name__ == "__main__":
    unittest.main()
