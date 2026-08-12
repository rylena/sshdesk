from __future__ import annotations

import argparse
import os
import shutil
import sys

from sshdesk.capture import SyntheticCapture
from sshdesk.render import TerminalRenderer, TerminalWriter


def _capture(name: str):
    if name == "synthetic":
        return SyntheticCapture()
    from sshdesk.capture.x11 import X11Capture

    return X11Capture()


def local_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one desktop frame in this terminal")
    parser.add_argument("--capture", choices=("x11", "synthetic"), default="x11")
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


def bench_main() -> int:
    from sshdesk.bench import bench_entrypoint

    return bench_entrypoint()


if __name__ == "__main__":
    raise SystemExit(main())
