from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path

from sshdesk.platform import create_capture
from sshdesk.render import TerminalRenderer, TerminalWriter


def _capture(name: str):
    return create_capture(name)


def local_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one desktop frame in this terminal")
    parser.add_argument(
        "--capture",
        choices=("auto", "x11", "wayland", "native", "synthetic"),
        default="auto",
    )
    parser.add_argument("--once", action="store_true", help="render once and exit")
    parser.add_argument("--columns", type=int)
    parser.add_argument("--rows", type=int)
    args = parser.parse_args(argv)
    terminal = shutil.get_terminal_size((80, 24))
    columns = args.columns or terminal.columns
    rows = args.rows or terminal.lines
    with _capture(args.capture) as capture:
        rendered = TerminalRenderer().render(capture.capture(), columns, rows)
    writer = TerminalWriter()
    stream = sys.stdout.buffer
    if not args.once and os.isatty(sys.stdout.fileno()):
        stream.write(writer.enter())
    stream.write(writer.full(rendered))
    if not args.once and os.isatty(sys.stdout.fileno()):
        stream.write(writer.leave())
    stream.flush()
    return 0


def main() -> int:
    return server_main()


def server_main() -> int:
    from sshdesk.session.server import server_entrypoint

    return server_entrypoint()


def _split_ssh_command(command: str) -> list[str] | None:
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return None


def _login_shell() -> str:
    configured = os.environ.get("COMSPEC", "cmd.exe") if os.name == "nt" else ""
    if os.name != "nt":
        try:
            import pwd

            configured = pwd.getpwuid(os.getuid()).pw_shell
        except (ImportError, KeyError):
            configured = ""
        configured = configured or os.environ.get("SHELL", "/bin/sh")
    shell = shutil.which(configured)
    if not shell:
        raise OSError(f"login shell is unavailable: {configured}")
    return shell


def _exec_shell() -> int:
    shell = _login_shell()
    arguments = [shell] if os.name == "nt" else [shell, "-l"]
    os.execv(shell, arguments)
    return 0


def _has_interactive_terminal() -> bool:
    return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())


def forced_command_main() -> int:
    """Dispatch the portable OpenSSH forced command."""
    original_command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if original_command:
        command = _split_ssh_command(original_command)
        program = Path(command[0]).name if command else ""
        from sshdesk.agent import agent_ssh_entrypoint

        if program == "sshdesk-agent":
            return agent_ssh_entrypoint([original_command])
        if command and len(command) == 1 and command[0] in {
            "desktop",
            "sshdesk",
            "sshdesk-server",
        }:
            if not _has_interactive_terminal():
                print("SSHDESK requires an interactive SSH terminal (PTY).", file=sys.stderr)
                return 1
            return server_main()
        if command and len(command) == 1 and command[0] in {"shell", "sshdesk-shell"}:
            if not _has_interactive_terminal():
                print("The SSH shell selector requires an interactive terminal (PTY).", file=sys.stderr)
                return 1
            try:
                return _exec_shell()
            except OSError as exc:
                print(f"sshdesk-shell: {exc}", file=sys.stderr)
                return 1
        return agent_ssh_entrypoint([original_command])
    if not _has_interactive_terminal():
        print("SSHDESK requires an interactive SSH terminal (PTY).", file=sys.stderr)
        return 1
    return server_main()


def bench_main() -> int:
    from sshdesk.bench import bench_entrypoint

    return bench_entrypoint()


if __name__ == "__main__":
    raise SystemExit(main())
