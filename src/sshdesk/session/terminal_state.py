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
        if os.name == "posix":
            import termios
            import tty

            self._attributes = termios.tcgetattr(self.input_fd)
            tty.setraw(self.input_fd, termios.TCSANOW)
        elif os.name == "nt":
            self._enter_windows_console()
        else:
            raise RuntimeError(f"unsupported terminal platform: {os.name}")
        self.active = True
        try:
            self.write_all(self.output_fd, self.writer.enter())
        except BaseException:
            self.restore()
            raise
        return self

    def _enter_windows_console(self) -> None:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_mode = kernel32.GetConsoleMode
        get_mode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_mode.restype = wintypes.BOOL
        set_mode = kernel32.SetConsoleMode
        set_mode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        set_mode.restype = wintypes.BOOL
        input_handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.input_fd))
        output_handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.output_fd))
        input_mode = wintypes.DWORD()
        output_mode = wintypes.DWORD()
        if not get_mode(input_handle, ctypes.byref(input_mode)) or not get_mode(
            output_handle, ctypes.byref(output_mode)
        ):
            raise RuntimeError("SSHDESK needs a Windows console or OpenSSH ConPTY")

        # Raw VT input and ANSI output. Preserve both modes for crash-safe cleanup.
        raw_input = input_mode.value & ~(0x0001 | 0x0002 | 0x0004 | 0x0040)
        raw_input |= 0x0080 | 0x0200
        vt_output = output_mode.value | 0x0001 | 0x0004
        if not set_mode(input_handle, raw_input):
            raise OSError(ctypes.get_last_error(), "could not enable Windows terminal raw mode")
        if not set_mode(output_handle, vt_output):
            set_mode(input_handle, input_mode.value)
            raise OSError(ctypes.get_last_error(), "could not enable Windows terminal ANSI mode")
        self._attributes = (kernel32, input_handle, input_mode.value, output_handle, output_mode.value)

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
            if self._attributes is not None and os.name == "posix":
                import termios

                termios.tcsetattr(self.input_fd, termios.TCSANOW, self._attributes)
            elif self._attributes is not None and os.name == "nt":
                kernel32, input_handle, input_mode, output_handle, output_mode = self._attributes
                kernel32.SetConsoleMode(input_handle, input_mode)
                kernel32.SetConsoleMode(output_handle, output_mode)
            self.active = False
        if write_error is not None:
            raise write_error

    def __exit__(self, *_: object) -> None:
        self.restore()
