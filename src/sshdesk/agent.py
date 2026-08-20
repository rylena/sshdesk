from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shlex
import sys
import time
from pathlib import Path

from PIL import Image

from sshdesk.capture.base import ScreenCapture
from sshdesk.input.base import InputBackend
from sshdesk.input.events import KeyCode, KeyEvent, Modifiers
from sshdesk.platform import create_capture, create_input, detect_platform

MAX_COMMAND_LENGTH = 65_536
MAX_TEXT_LENGTH = 16_384
BUTTONS = {"left": 1, "middle": 2, "right": 3}
KEY_NAMES = {key.name.lower().replace("_", "-"): key for key in KeyCode if key != KeyCode.CHARACTER}


class AgentController:
    """Low-frequency computer-use commands for an authenticated SSH agent."""

    def __init__(self) -> None:
        self._capture: ScreenCapture | None = None
        self._input: InputBackend | None = None

    @property
    def capture(self) -> ScreenCapture:
        if self._capture is None:
            self._capture = create_capture()
        return self._capture

    @property
    def input(self) -> InputBackend:
        if self._input is None:
            self._input = create_input(capture=self.capture)
        return self._input

    def info(self) -> dict[str, object]:
        selected = detect_platform()
        width, height = self.capture.size()
        return {
            "platform": selected.system,
            "session": selected.session,
            "capture": selected.capture,
            "input": selected.input,
            "width": width,
            "height": height,
        }

    def screenshot(self, max_width: int = 0) -> tuple[bytes, int, int]:
        frame = self.capture.capture()
        image = frame.image
        if max_width and image.width > max_width:
            height = max(1, round(image.height * max_width / image.width))
            image = image.resize((max_width, height), Image.Resampling.BILINEAR)
        output = io.BytesIO()
        image.save(output, format="PNG", compress_level=1)
        return output.getvalue(), frame.width, frame.height

    def move(self, x: int, y: int) -> None:
        self.input.move(x, y)

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        number = BUTTONS[button]
        bounded_count = max(1, min(count, 20))
        for index in range(bounded_count):
            self.input.button(number, True, x, y)
            self.input.button(number, False, x, y)
            if index + 1 < bounded_count:
                time.sleep(0.05)

    def scroll(self, amount: int, x: int, y: int) -> None:
        self.input.scroll(max(-20, min(20, amount)), x, y)

    def type_text(self, text: str, interval_ms: float = 0.0) -> None:
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"text is limited to {MAX_TEXT_LENGTH} characters")
        delay = max(0.0, min(interval_ms, 1000.0)) / 1000
        for character in text:
            self.input.key(KeyEvent(2, 0, int(KeyCode.CHARACTER), ord(character)))
            if delay:
                time.sleep(delay)

    def key(self, name: str, modifiers: int = 0) -> None:
        try:
            key = KEY_NAMES[name.lower()]
        except KeyError as exc:
            raise ValueError(f"unknown key: {name}") from exc
        self.input.key(KeyEvent(2, modifiers, int(key)))

    def close(self) -> None:
        if self._input is not None:
            self._input.close()
            self._input = None
        if self._capture is not None:
            self._capture.close()
            self._capture = None

    def __enter__(self) -> AgentController:  # noqa: PYI034
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _modifiers(value: dict[str, object] | argparse.Namespace) -> int:
    get = value.get if isinstance(value, dict) else lambda name, default=False: getattr(value, name, default)
    result = Modifiers.NONE
    if get("ctrl", False):
        result |= Modifiers.CTRL
    if get("alt", False):
        result |= Modifiers.ALT
    if get("shift", False):
        result |= Modifiers.SHIFT
    return int(result)


def _bounded_coordinate(value: object) -> int:
    parsed = int(value)
    if not -16384 <= parsed <= 65535:
        raise ValueError("coordinate is outside the supported range")
    return parsed


def _session_action(controller: AgentController, request: dict[str, object]) -> dict[str, object]:
    action = str(request.get("action", ""))
    if action == "info":
        return controller.info()
    if action in {"observe", "screenshot"}:
        maximum = max(0, min(int(request.get("max_width", 0)), 4096))
        image, width, height = controller.screenshot(maximum)
        return {
            "width": width,
            "height": height,
            "format": "png",
            "image_base64": base64.b64encode(image).decode("ascii"),
        }
    if action == "move":
        controller.move(_bounded_coordinate(request["x"]), _bounded_coordinate(request["y"]))
        return {}
    if action == "click":
        button = str(request.get("button", "left"))
        if button not in BUTTONS:
            raise ValueError("button must be left, middle, or right")
        controller.click(
            _bounded_coordinate(request["x"]),
            _bounded_coordinate(request["y"]),
            button,
            max(1, min(int(request.get("count", 1)), 20)),
        )
        return {}
    if action == "scroll":
        controller.scroll(
            int(request["amount"]),
            _bounded_coordinate(request["x"]),
            _bounded_coordinate(request["y"]),
        )
        return {}
    if action == "type":
        controller.type_text(str(request.get("text", "")), float(request.get("interval_ms", 0)))
        return {}
    if action == "key":
        controller.key(str(request["key"]), _modifiers(request))
        return {}
    if action == "wait":
        time.sleep(max(0.0, min(float(request.get("seconds", 0)), 10.0)))
        return {}
    if action == "quit":
        return {"quit": True}
    raise ValueError(f"unknown action: {action}")


def run_agent_session(controller: AgentController) -> int:
    for raw_line in sys.stdin.buffer:
        if len(raw_line) > MAX_COMMAND_LENGTH:
            response: dict[str, object] = {"ok": False, "error": "request is too large"}
        else:
            request_id: object = None
            try:
                decoded = json.loads(raw_line)
                if not isinstance(decoded, dict):
                    raise TypeError("request must be a JSON object")
                request_id = decoded.get("id")
                result = _session_action(controller, decoded)
                response = {"id": request_id, "ok": True, **result}
            except (
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                response = {"id": request_id, "ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        if response.get("quit"):
            return 0
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sshdesk-agent",
        description="Observe and control the active desktop through SSHDESK",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("info", help="print platform and desktop information")
    screenshot = subparsers.add_parser("screenshot", aliases=["observe"])
    screenshot.add_argument("--max-width", type=int, default=0)
    screenshot.add_argument("--output", default="-", help="PNG path or - for stdout")
    move = subparsers.add_parser("move")
    move.add_argument("x", type=int)
    move.add_argument("y", type=int)
    click = subparsers.add_parser("click")
    click.add_argument("x", type=int)
    click.add_argument("y", type=int)
    click.add_argument("--button", choices=tuple(BUTTONS), default="left")
    click.add_argument("--count", type=int, default=1)
    scroll = subparsers.add_parser("scroll")
    scroll.add_argument("amount", type=int)
    scroll.add_argument("x", type=int)
    scroll.add_argument("y", type=int)
    type_command = subparsers.add_parser("type")
    type_command.add_argument("text")
    type_command.add_argument("--interval-ms", type=float, default=0)
    key = subparsers.add_parser("key")
    key.add_argument("key", choices=tuple(KEY_NAMES))
    key.add_argument("--ctrl", action="store_true")
    key.add_argument("--alt", action="store_true")
    key.add_argument("--shift", action="store_true")
    subparsers.add_parser("session", help="serve newline-delimited computer-use requests")
    return parser


def agent_entrypoint(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with AgentController() as controller:
            if args.command == "info":
                print(json.dumps(controller.info(), separators=(",", ":")))
            elif args.command in {"screenshot", "observe"}:
                if not 0 <= args.max_width <= 4096:
                    raise ValueError("--max-width must be between 0 and 4096")
                image, _width, _height = controller.screenshot(args.max_width)
                if args.output == "-":
                    sys.stdout.buffer.write(image)
                    sys.stdout.buffer.flush()
                else:
                    Path(args.output).write_bytes(image)
            elif args.command == "move":
                controller.move(_bounded_coordinate(args.x), _bounded_coordinate(args.y))
            elif args.command == "click":
                controller.click(
                    _bounded_coordinate(args.x),
                    _bounded_coordinate(args.y),
                    args.button,
                    args.count,
                )
            elif args.command == "scroll":
                controller.scroll(
                    args.amount,
                    _bounded_coordinate(args.x),
                    _bounded_coordinate(args.y),
                )
            elif args.command == "type":
                controller.type_text(args.text, args.interval_ms)
            elif args.command == "key":
                controller.key(args.key, _modifiers(args))
            elif args.command == "session":
                return run_agent_session(controller)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"sshdesk-agent: {exc}", file=sys.stderr)
        return 1


def agent_ssh_entrypoint(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if len(values) != 1 or len(values[0]) > MAX_COMMAND_LENGTH:
        print("sshdesk-agent: invalid SSH command", file=sys.stderr)
        return 2
    try:
        command = shlex.split(values[0], posix=os.name != "nt")
    except ValueError as exc:
        print(f"sshdesk-agent: invalid SSH command: {exc}", file=sys.stderr)
        return 2
    if not command or Path(command[0]).name != "sshdesk-agent":
        print(
            "This account accepts only SSHDESK desktop, shell selector, or agent commands.",
            file=sys.stderr,
        )
        return 126
    if "--output" in command[1:] or any(
        value.startswith("--output=") for value in command[1:]
    ):
        print("sshdesk-agent: remote screenshots are written only to stdout", file=sys.stderr)
        return 2
    try:
        return agent_entrypoint(command[1:])
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2


if __name__ == "__main__":
    raise SystemExit(agent_entrypoint())
