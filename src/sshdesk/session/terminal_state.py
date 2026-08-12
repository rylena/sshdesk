from __future__ import annotations

import os
import shutil
import sys
from typing import Any

from sshdesk.render.writer import TerminalWriter


class TerminalState:
    """Raw terminal + alternate-screen lifecycle with guaranteed restoration."""

    def __init__(
        self,
        input_fd: int | None = None,
        output_fd: int | None = None,
        writer: TerminalWriter | None = None,
    ) -> None:
        self.input_fd = sys.stdin.fileno() if input_fd is None else input_fd
        self.output_fd = sys.stdout.fileno() if output_fd is None else output_fd
        self._attributes: Any = None
        self.writer = writer or TerminalWriter()
        self.active = False

    @staticmethod
    def size(fd: int | None = None) -> tuple[int, int]:
        try:
            terminal = os.get_terminal_size(sys.stdout.fileno() if fd is None else fd)
        except OSError:
            terminal = shutil.get_terminal_size((80, 24))
        return max(1, terminal.columns), max(1, terminal.lines)

    @staticmethod
    def write_all(fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                written = os.write(fd, view)
            except BlockingIOError:
                import select

                select.select((), (fd,), (), 0.1)
                continue
            except InterruptedError:
                continue
            view = view[written:]

    def __enter__(self) -> TerminalState:  # noqa: PYI034 - Python 3.10 lacks typing.Self
        if not os.isatty(self.input_fd) or not os.isatty(self.output_fd):
            raise RuntimeError("SSHDESK client requires an interactive terminal")
        if os.name != "posix":
            raise RuntimeError("sshdesk-server currently requires a Linux/POSIX server")
        import termios
        import tty

        self._attributes = termios.tcgetattr(self.input_fd)
        tty.setraw(self.input_fd, termios.TCSANOW)
        self.active = True
        try:
            self.write_all(self.output_fd, self.writer.enter())
        except BaseException:
            self.restore()
            raise
        return self

    def restore(self) -> None:
        if not self.active:
            return
        write_error: OSError | None = None
        try:
            try:
                self.write_all(self.output_fd, self.writer.leave())
            except OSError as exc:
                write_error = exc
        finally:
            if self._attributes is not None:
                import termios

                termios.tcsetattr(self.input_fd, termios.TCSANOW, self._attributes)
            self.active = False
        if write_error is not None:
            raise write_error

    def __exit__(self, *_: object) -> None:
        self.restore()
