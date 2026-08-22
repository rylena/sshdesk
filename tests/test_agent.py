from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sshdesk.agent import (
    AgentController,
    _session_action,
    agent_ssh_entrypoint,
)
from sshdesk.capture.synthetic import SyntheticCapture
from sshdesk.cli import _shell_arguments, forced_command_main
from sshdesk.client import _remote_request, _split_arguments
from sshdesk.input.base import InputBackend
from sshdesk.input.events import KeyCode, KeyEvent, Modifiers
from sshdesk.input.ydotool import YdotoolInput
from sshdesk.platform import detect_platform


class RecordingInput(InputBackend):
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def key(self, event: KeyEvent) -> None:
        self.events.append(("key", event))

    def move(self, x: int, y: int) -> None:
        self.events.append(("move", x, y))

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        self.events.append(("button", button, pressed, x, y))

    def scroll(self, amount: int, x: int, y: int) -> None:
        self.events.append(("scroll", amount, x, y))


class AgentTests(unittest.TestCase):
    def controller(self) -> tuple[AgentController, RecordingInput]:
        controller = AgentController()
        backend = RecordingInput()
        controller._capture = SyntheticCapture(320, 180, animate=False)
        controller._input = backend
        return controller, backend

    def test_observe_and_computer_use_actions(self) -> None:
        controller, backend = self.controller()
        result = _session_action(controller, {"action": "observe", "max_width": 160})
        self.assertEqual((result["width"], result["height"]), (320, 180))
        self.assertTrue(str(result["image_base64"]).startswith("iVBOR"))

        _session_action(controller, {"action": "click", "x": 12, "y": 34})
        _session_action(controller, {"action": "scroll", "amount": -3, "x": 12, "y": 34})
        _session_action(controller, {"action": "type", "text": "Hi"})
        _session_action(controller, {"action": "key", "key": "enter", "ctrl": True})
        self.assertIn(("button", 1, True, 12, 34), backend.events)
        self.assertIn(("button", 1, False, 12, 34), backend.events)
        self.assertIn(("scroll", -3, 12, 34), backend.events)
        self.assertEqual(
            [event[1].unicode for event in backend.events if event[0] == "key"][:2],
            [ord("H"), ord("i")],
        )
        last = backend.events[-1][1]
        self.assertEqual(last.key_code, int(KeyCode.ENTER))
        self.assertEqual(last.modifiers, int(Modifiers.CTRL))

    def test_agent_rejects_untrusted_shell_commands(self) -> None:
        self.assertEqual(agent_ssh_entrypoint(["id"]), 126)
        self.assertEqual(agent_ssh_entrypoint(["sshdesk-agent info; id"]), 2)
        self.assertEqual(agent_ssh_entrypoint(["sshdesk-agent && id"]), 2)
        self.assertEqual(
            agent_ssh_entrypoint(["sshdesk-agent screenshot --output /tmp/capture.png"]),
            2,
        )

    def test_portable_forced_command_routes_only_agent_grammar(self) -> None:
        with patch.dict("os.environ", {"SSH_ORIGINAL_COMMAND": "id"}, clear=True):
            self.assertEqual(forced_command_main(), 126)

    def test_portable_forced_command_opens_shell_selector(self) -> None:
        environment = {"SSH_ORIGINAL_COMMAND": "shell"}
        with patch.dict("os.environ", environment, clear=True), patch(
            "sshdesk.cli._has_interactive_terminal", return_value=True
        ), patch("sshdesk.cli._login_shell", return_value="/bin/bash"), patch(
            "sshdesk.cli.os.execv"
        ) as execv:
            self.assertEqual(forced_command_main(), 0)
        arguments = ["/bin/bash"] if os.name == "nt" else ["/bin/bash", "-l"]
        execv.assert_called_once_with("/bin/bash", arguments)

    def test_login_shell_arguments_match_each_platform(self) -> None:
        # Regression test for the Windows portable job: cmd.exe rejects -l.
        with patch("sshdesk.cli.os.name", "posix"):
            self.assertEqual(_shell_arguments("/bin/bash"), ["/bin/bash", "-l"])
        with patch("sshdesk.cli.os.name", "nt"):
            self.assertEqual(_shell_arguments("/bin/bash"), ["/bin/bash"])
            self.assertEqual(_shell_arguments("cmd.exe"), ["cmd.exe"])

    def test_windows_shell_selector_execs_without_login_flag(self) -> None:
        environment = {"SSH_ORIGINAL_COMMAND": "shell"}
        shell = "C:/Windows/system32/cmd.exe"
        with patch.dict("os.environ", environment, clear=True), patch(
            "sshdesk.cli.os.name", "nt"
        ), patch(
            "sshdesk.cli._has_interactive_terminal", return_value=True
        ), patch(
            "sshdesk.cli._login_shell", return_value=shell
        ), patch("sshdesk.cli.os.execv") as execv:
            self.assertEqual(forced_command_main(), 0)
        execv.assert_called_once_with(shell, [shell])

    def test_portable_forced_command_requires_pty_for_shell_selector(self) -> None:
        environment = {"SSH_ORIGINAL_COMMAND": "shell"}
        with patch.dict("os.environ", environment, clear=True), patch(
            "sshdesk.cli._has_interactive_terminal", return_value=False
        ), patch("sshdesk.cli.os.execv") as execv:
            self.assertEqual(forced_command_main(), 1)
        execv.assert_not_called()

    def test_portable_forced_command_keeps_agent_commands_restricted(self) -> None:
        environment = {"SSH_ORIGINAL_COMMAND": "sshdesk-agent info; id"}
        with patch.dict("os.environ", environment, clear=True), patch(
            "sshdesk.cli.os.execv"
        ) as execv:
            self.assertEqual(forced_command_main(), 2)
        execv.assert_not_called()

    def test_portable_forced_command_accepts_explicit_desktop_command(self) -> None:
        with patch.dict(
            "os.environ", {"SSH_ORIGINAL_COMMAND": "sshdesk"}, clear=True
        ), patch("sshdesk.cli._has_interactive_terminal", return_value=True), patch(
            "sshdesk.cli.server_main", return_value=0
        ) as server:
            self.assertEqual(forced_command_main(), 0)
        server.assert_called_once_with()

    def test_actions_are_bounded(self) -> None:
        controller, _backend = self.controller()
        with self.assertRaisesRegex(ValueError, "coordinate"):
            _session_action(controller, {"action": "move", "x": 999999, "y": 0})
        with self.assertRaisesRegex(ValueError, "button"):
            _session_action(
                controller,
                {"action": "click", "x": 0, "y": 0, "button": "extra"},
            )

    def test_ydotool_adds_shift_for_uppercase(self) -> None:
        backend = object.__new__(YdotoolInput)
        backend._pressed_buttons = set()
        backend._pressed_keys = set()
        calls: list[tuple[str, ...]] = []
        backend._run = lambda *values: calls.append(values)
        backend.key(KeyEvent(2, 0, int(KeyCode.CHARACTER), ord("A")))
        self.assertIn("42:1", calls[0])
        self.assertIn("42:0", calls[0])

    def test_ydotool_checks_daemon_without_injecting_input(self) -> None:
        completed = SimpleNamespace(returncode=0, stderr=b"")
        with patch("sshdesk.input.ydotool.shutil.which", return_value="/usr/bin/ydotool"), patch(
            "sshdesk.input.ydotool.subprocess.run", return_value=completed
        ) as run:
            YdotoolInput()
        self.assertEqual(run.call_args.args[0], ["/usr/bin/ydotool", "debug"])

    def test_platform_selects_x11_and_wayland(self) -> None:
        with patch("sshdesk.platform.platform.system", return_value="Linux"), patch.dict(
            "os.environ", {"DISPLAY": ":1", "XDG_SESSION_TYPE": "x11"}, clear=True
        ):
            self.assertEqual(detect_platform().capture, "x11")
        with patch("sshdesk.platform.platform.system", return_value="Linux"), patch.dict(
            "os.environ",
            {"DISPLAY": ":1", "WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"},
            clear=True,
        ):
            selected = detect_platform()
            self.assertEqual((selected.capture, selected.input), ("wayland", "ydotool"))
        with patch("sshdesk.platform.platform.system", return_value="Linux"), patch.dict(
            "os.environ",
            {
                "WAYLAND_DISPLAY": "wayland-0",
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "GNOME",
            },
            clear=True,
        ):
            selected = detect_platform()
            self.assertEqual((selected.capture, selected.input), ("gnome", "mutter"))
        with patch("sshdesk.platform.platform.system", return_value="Darwin"):
            self.assertEqual((detect_platform().capture, detect_platform().input), ("native", "quartz"))
        with patch("sshdesk.platform.platform.system", return_value="Windows"):
            self.assertEqual(
                (detect_platform().capture, detect_platform().input),
                ("native", "sendinput"),
            )

    def test_split_command_is_an_argument_vector(self) -> None:
        self.assertEqual(
            _split_arguments("alice@example.com", "right", 50, "%3"),
            ["split-window", "-h", "-p", "50", "-t", "%3", "--", "ssh", "alice@example.com"],
        )

    def test_remote_request_uses_only_fixed_ssh_command(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout=b'{"id":1,"ok":true,"width":320}\n',
            stderr=b"",
        )
        with patch("sshdesk.client.shutil.which", return_value="/usr/bin/ssh"), patch(
            "sshdesk.client.subprocess.run", return_value=completed
        ) as run:
            response = _remote_request("alice@example.com", {"id": 1, "action": "info"})
        self.assertEqual(response["width"], 320)
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/ssh", "alice@example.com", "sshdesk-agent", "session"],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 30.0)


if __name__ == "__main__":
    unittest.main()
