from __future__ import annotations

import os
import select
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

if os.name == "posix":
    import fcntl
    import pty
    import termios

from sshdesk.render import ColorMode, TerminalCapabilities, TerminalWriter
from sshdesk.session.terminal_state import TerminalState

ROOT = Path(__file__).resolve().parents[1]


class LifecycleTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX terminal test")
    def test_terminal_restored_after_exception(self) -> None:
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        writer = TerminalWriter(
            TerminalCapabilities("test", ColorMode.ANSI256, True, True, True)
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "crash"), TerminalState(
                slave, slave, writer
            ):
                raise RuntimeError("simulated crash")
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(slave)
            os.close(master)

    @unittest.skipUnless(os.name == "posix", "POSIX terminal test")
    def test_plain_pty_session_detaches_and_restores(self) -> None:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        before = termios.tcgetattr(slave)
        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TERM": "xterm-256color",
            "SSHDESK_COLOR": "256",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sshdesk.session.server",
                "--capture",
                "synthetic",
                "--synthetic-static",
                "--no-input",
            ],
            cwd=ROOT,
            env=environment,
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        output = bytearray()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and b"\x1b[?1049h" not in output:
                ready, _, _ = select.select((master,), (), (), 0.1)
                if ready:
                    output.extend(os.read(master, 65536))
            self.assertIn(b"\x1b[?1049h", output)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            resize_deadline = time.monotonic() + 3
            while time.monotonic() < resize_deadline and output.count(b"\x1b[2J") < 2:
                ready, _, _ = select.select((master,), (), (), 0.1)
                if ready:
                    output.extend(os.read(master, 65536))
            self.assertGreaterEqual(output.count(b"\x1b[2J"), 2)
            os.write(master, b"\x1d\x1d")
            deadline = time.monotonic() + 5
            while process.poll() is None and time.monotonic() < deadline:
                ready, _, _ = select.select((master,), (), (), 0.1)
                if ready:
                    output.extend(os.read(master, 65536))
            self.assertEqual(process.wait(timeout=1), 0, process.stderr.read().decode())
            while True:
                ready, _, _ = select.select((master,), (), (), 0)
                if not ready:
                    break
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
            self.assertIn(b"\x1b[?1049l", output)
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            if process.stderr is not None:
                process.stderr.close()
            os.close(slave)
            os.close(master)

    @unittest.skipUnless(os.name == "posix", "POSIX terminal test")
    def test_kitty_probe_selects_real_pixel_session(self) -> None:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 40, 0, 0))
        before = termios.tcgetattr(slave)
        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TERM": "xterm-kitty",
            "SSHDESK_RENDER": "auto",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sshdesk.session.server",
                "--capture",
                "synthetic",
                "--synthetic-static",
                "--no-input",
            ],
            cwd=ROOT,
            env=environment,
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        output = bytearray()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and b"a=q,t=d,f=24" not in output:
                ready, _, _ = select.select((master,), (), (), 0.1)
                if ready:
                    output.extend(os.read(master, 65536))
            self.assertIn(b"a=q,t=d,f=24", output)
            os.write(
                master,
                b"\x1b_Gi=1893;OK\x1b\\"
                b"\x1b[4;192;320t\x1b[6;16;8t"
                b"\x1b[?1016;2$y\x1b[?2026;2$y\x1b[?62;c",
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and b"\x1b_Ga=T" not in output:
                ready, _, _ = select.select((master,), (), (), 0.1)
                if ready:
                    output.extend(os.read(master, 65536))
            self.assertIn(b"\x1b_Ga=T", output)
            self.assertIn(b"o=z", output)
            self.assertIn(b"\x1b[?1016h", output)
            os.write(master, b"\x1d\x1d")
            self.assertEqual(process.wait(timeout=5), 0, process.stderr.read().decode())
            while True:
                ready, _, _ = select.select((master,), (), (), 0)
                if not ready:
                    break
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
            self.assertIn(b"\x1b_Ga=d,d=A,q=2", output)
            self.assertIn(b"\x1b[?1016l", output)
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            if process.stderr is not None:
                process.stderr.close()
            os.close(slave)
            os.close(master)

    def test_server_rejects_non_pty_session_cleanly(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "sshdesk.session.server",
                "--capture",
                "synthetic",
                "--synthetic-static",
                "--no-input",
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "TERM": "xterm-256color",
            },
            input=b"",
            capture_output=True,
            check=False,
            timeout=3,
        )
        self.assertEqual(process.returncode, 1)
        self.assertIn(b"interactive terminal", process.stderr)


if __name__ == "__main__":
    unittest.main()
