from __future__ import annotations

import io
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sshdesk.agent import (
    MAX_COMMAND_LENGTH,
    AgentController,
    run_agent_session,
)
from sshdesk.capture.synthetic import SyntheticCapture
from sshdesk.input.base import InputBackend
from sshdesk.session.server import server_entrypoint


class RecordingInput(InputBackend):
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def key(self, event: object) -> None:
        self.events.append(("key", event))

    def move(self, x: int, y: int) -> None:
        self.events.append(("move", x, y))

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        self.events.append(("button", button, pressed, x, y))

    def scroll(self, amount: int, x: int, y: int) -> None:
        self.events.append(("scroll", amount, x, y))


class AgentSessionTests(unittest.TestCase):
    def controller(self) -> tuple[AgentController, RecordingInput]:
        controller = AgentController()
        backend = RecordingInput()
        controller._capture = SyntheticCapture(320, 180, animate=False)
        controller._input = backend
        return controller, backend

    def run_session(
        self,
        controller: AgentController,
        lines: list[str],
    ) -> tuple[int, list[dict[str, object]]]:
        payload = "".join(line + "\n" for line in lines).encode()
        output = io.BytesIO()
        wrapper = io.TextIOWrapper(output)
        with patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload))), patch.object(
            sys, "stdout", wrapper
        ):
            code = run_agent_session(controller)
        wrapper.detach()
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_info_and_quit_stop_the_session(self) -> None:
        controller, _backend = self.controller()
        quit_line = json.dumps({"id": 1, "action": "quit"})
        info_line = json.dumps({"id": 2, "action": "info"})
        platform = SimpleNamespace(
            system="Linux", session="x11", capture="synthetic", input="null"
        )
        with patch("sshdesk.agent.detect_platform", return_value=platform):
            code, responses = self.run_session(controller, [info_line, quit_line, info_line])
        self.assertEqual(code, 0)
        self.assertEqual(len(responses), 2)
        self.assertTrue(responses[0]["ok"])
        self.assertEqual(responses[0]["width"], 320)
        self.assertTrue(responses[1]["quit"])

    def test_errors_keep_the_session_alive(self) -> None:
        controller, backend = self.controller()
        lines = [
            "{not json",
            "[1, 2]",
            json.dumps({"id": 3, "action": "teleport"}),
            json.dumps({"id": 4, "action": "move", "x": 999_999, "y": 0}),
            json.dumps({"id": 5, "action": "move", "x": 7, "y": 9}),
        ]
        code, responses = self.run_session(controller, lines)
        self.assertEqual(code, 0)
        self.assertEqual(len(responses), len(lines))
        self.assertFalse(responses[0]["ok"])
        self.assertIsNone(responses[0]["id"])
        self.assertIn("JSON object", responses[1]["error"])
        self.assertIn("unknown action", responses[2]["error"])
        self.assertIn("coordinate", responses[3]["error"])
        self.assertTrue(responses[4]["ok"])
        self.assertIn(("move", 7, 9), backend.events)

    def test_oversized_requests_are_rejected_without_parsing(self) -> None:
        controller, backend = self.controller()
        oversized = "x" * (MAX_COMMAND_LENGTH + 1)
        code, responses = self.run_session(
            controller,
            [oversized, json.dumps({"id": 6, "action": "quit"})],
        )
        self.assertEqual(code, 0)
        self.assertEqual(responses[0], {"ok": False, "error": "request is too large"})
        self.assertEqual(backend.events, [])


class ServerInterruptTests(unittest.TestCase):
    def test_keyboard_interrupt_during_startup_exits_cleanly(self) -> None:
        with patch("sshdesk.session.server.create_capture", side_effect=KeyboardInterrupt):
            self.assertEqual(server_entrypoint([]), 130)

    def test_keyboard_interrupt_from_session_exits_cleanly(self) -> None:
        with patch.dict("os.environ", {"TERM": "xterm-256color"}), patch(
            "sshdesk.session.direct.DirectSession.run",
            side_effect=KeyboardInterrupt,
        ):
            self.assertEqual(
                server_entrypoint(["--capture", "synthetic", "--no-input"]),
                130,
            )


if __name__ == "__main__":
    unittest.main()
