from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from sshdesk.capture.base import ScreenCapture
from sshdesk.capture.synthetic import SyntheticCapture
from sshdesk.input.base import InputBackend, NullInputBackend
from sshdesk.platform import create_capture as create_platform_capture
from sshdesk.platform import create_input as create_platform_input
from sshdesk.render import TerminalCapabilities


@dataclass(frozen=True, slots=True)
class ServerConfig:
    capture: str = "auto"
    input: str = "auto"
    display: str | None = None
    input_enabled: bool = True
    synthetic_animate: bool = True


def create_capture(config: ServerConfig) -> ScreenCapture:
    if config.capture == "synthetic":
        return SyntheticCapture(1280, 720, config.synthetic_animate)
    return create_platform_capture(config.capture, config.display)


def create_input(config: ServerConfig) -> InputBackend:
    if not config.input_enabled or config.capture == "synthetic":
        return NullInputBackend()
    return create_platform_input(config.input, config.display)


def _capabilities(args: argparse.Namespace) -> TerminalCapabilities:
    environment = dict(os.environ)
    if args.color != "auto":
        environment["SSHDESK_COLOR"] = args.color
    if args.no_mouse:
        environment["SSHDESK_MOUSE"] = "0"
    if args.ascii:
        environment["SSHDESK_UNICODE"] = "0"
    return TerminalCapabilities.detect(environment)


def server_entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the active desktop directly in an ordinary SSH terminal"
    )
    parser.add_argument(
        "--capture",
        choices=("auto", "x11", "wayland", "native", "synthetic"),
        default="auto",
    )
    parser.add_argument(
        "--input",
        choices=("auto", "x11", "ydotool", "quartz", "sendinput", "none"),
        default="auto",
    )
    parser.add_argument("--display", help="X11 display (defaults to DISPLAY)")
    parser.add_argument("--no-input", action="store_true", help="view-only session")
    parser.add_argument("--synthetic-static", action="store_true")
    parser.add_argument("--color", choices=("auto", "truecolor", "256", "16"), default="auto")
    parser.add_argument("--no-mouse", action="store_true", help="keyboard-only mode")
    parser.add_argument("--ascii", action="store_true", help="avoid Unicode half-block glyphs")
    parser.add_argument(
        "--max-fps",
        type=float,
        help="active refresh limit (default: 60 sharp pixels, 30 ANSI; maximum 120)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify capture and input access without starting a terminal session",
    )
    args = parser.parse_args(argv)
    config = ServerConfig(
        capture=args.capture,
        input=args.input,
        display=args.display,
        input_enabled=not args.no_input,
        synthetic_animate=not args.synthetic_static,
    )
    capture: ScreenCapture | None = None
    input_backend: InputBackend | None = None
    try:
        capture = create_capture(config)
        input_backend = create_input(config)
        if args.check:
            frame = capture.capture()
            print(
                f"SSHDESK check passed: {frame.width}x{frame.height} "
                f"{type(capture).__name__} capture; "
                f"input={'enabled' if config.input_enabled else 'disabled'}"
            )
            return 0
        from .direct import DirectSession

        session = DirectSession(
            capture,
            input_backend,
            _capabilities(args),
            max_fps=args.max_fps,
        )
        # Ownership transfers to the session, including failure cleanup.
        capture = None
        input_backend = None
        return session.run()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"sshdesk-server: {exc}", file=sys.stderr)
        return 1
    finally:
        if input_backend is not None:
            input_backend.close()
        if capture is not None:
            capture.close()


if __name__ == "__main__":
    raise SystemExit(server_entrypoint())
