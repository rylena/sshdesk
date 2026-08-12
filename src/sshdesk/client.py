from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from sshdesk.agent import BUTTONS, KEY_NAMES

TARGET_RE = re.compile(r"^[A-Za-z0-9_.%+@:-]{1,255}$")
MAX_REMOTE_RESPONSE = 64 * 1024 * 1024


def _split_arguments(target: str, direction: str, size: int, pane: str) -> list[str]:
    sideways = direction in {"left", "right"}
    arguments = ["split-window", "-h" if sideways else "-v"]
    if direction in {"left", "up"}:
        arguments.append("-b")
    arguments.extend(["-p", str(size), "-t", pane, "--", "ssh", target])
    return arguments


def split_entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sshdesk-split",
        description="Open an agent shell and SSHDESK side by side in tmux",
    )
    parser.add_argument("target", help="OpenSSH target, for example user@host")
    parser.add_argument(
        "--direction", choices=("right", "left", "down", "up"), default="right"
    )
    parser.add_argument("--size", type=int, default=50, help="desktop pane percentage")
    args = parser.parse_args(argv)
    if not TARGET_RE.fullmatch(args.target):
        parser.error("target contains unsupported characters")
    if not 20 <= args.size <= 80:
        parser.error("--size must be between 20 and 80")
    tmux = shutil.which("tmux")
    if tmux is None:
        parser.error("tmux is required for portable split-pane mode")

    if os.environ.get("TMUX"):
        pane = os.environ.get("TMUX_PANE", "")
        if not re.fullmatch(r"%[0-9]+", pane):
            parser.error("cannot identify the current tmux pane")
        subprocess.run(
            [tmux, "set-option", "-p", "-t", pane, "allow-passthrough", "on"],
            check=True,
        )
        return subprocess.run(
            [tmux, *_split_arguments(args.target, args.direction, args.size, pane)],
            check=False,
        ).returncode

    session = f"sshdesk-{os.getpid()}"
    subprocess.run([tmux, "new-session", "-d", "-s", session], check=True)
    try:
        subprocess.run(
            [tmux, "set-option", "-t", session, "allow-passthrough", "on"], check=True
        )
        subprocess.run([tmux, "set-option", "-t", session, "focus-events", "on"], check=True)
        subprocess.run(
            [tmux, *_split_arguments(args.target, args.direction, args.size, f"{session}:0.0")],
            check=True,
        )
        return subprocess.run([tmux, "attach-session", "-t", session], check=False).returncode
    except BaseException:
        subprocess.run([tmux, "kill-session", "-t", session], check=False)
        raise


def _remote_request(target: str, request: dict[str, object]) -> dict[str, object]:
    ssh = shutil.which("ssh")
    if ssh is None:
        raise RuntimeError("OpenSSH ssh is required")
    payload = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    process = subprocess.run(
        [ssh, target, "sshdesk-agent", "session"],
        input=payload,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if process.returncode != 0:
        detail = process.stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or f"SSH exited with status {process.returncode}")
    if len(process.stdout) > MAX_REMOTE_RESPONSE:
        raise RuntimeError("remote response exceeded the safety limit")
    try:
        response = json.loads(process.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("remote returned an invalid agent response") from exc
    if not isinstance(response, dict):
        raise TypeError("remote returned an invalid agent response")
    if not response.get("ok"):
        raise RuntimeError(str(response.get("error", "remote action failed")))
    return response


def remote_entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sshdesk-remote",
        description="Observe or control an SSHDESK host through ordinary OpenSSH",
    )
    parser.add_argument("target", help="OpenSSH target, for example user@host")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info")
    screenshot = commands.add_parser("screenshot", aliases=["observe"])
    screenshot.add_argument("--max-width", type=int, default=0)
    screenshot.add_argument("--output", default="-")
    move = commands.add_parser("move")
    move.add_argument("x", type=int)
    move.add_argument("y", type=int)
    click = commands.add_parser("click")
    click.add_argument("x", type=int)
    click.add_argument("y", type=int)
    click.add_argument("--button", choices=tuple(BUTTONS), default="left")
    click.add_argument("--count", type=int, default=1)
    scroll = commands.add_parser("scroll")
    scroll.add_argument("amount", type=int)
    scroll.add_argument("x", type=int)
    scroll.add_argument("y", type=int)
    type_command = commands.add_parser("type")
    type_command.add_argument("text")
    type_command.add_argument("--interval-ms", type=float, default=0)
    key = commands.add_parser("key")
    key.add_argument("key", choices=tuple(KEY_NAMES))
    key.add_argument("--ctrl", action="store_true")
    key.add_argument("--alt", action="store_true")
    key.add_argument("--shift", action="store_true")
    commands.add_parser("session", help="stream newline-delimited JSON requests")
    args = parser.parse_args(argv)
    if not TARGET_RE.fullmatch(args.target):
        parser.error("target contains unsupported characters")
    if args.command == "session":
        ssh = shutil.which("ssh")
        if ssh is None:
            parser.error("OpenSSH ssh is required")
        return subprocess.run(
            [ssh, args.target, "sshdesk-agent", "session"], check=False
        ).returncode

    request: dict[str, object] = {"id": 1, "action": args.command}
    if args.command in {"screenshot", "observe"}:
        if not 0 <= args.max_width <= 4096:
            parser.error("--max-width must be between 0 and 4096")
        request.update(max_width=args.max_width)
    elif args.command == "move":
        request.update(x=args.x, y=args.y)
    elif args.command == "click":
        request.update(x=args.x, y=args.y, button=args.button, count=args.count)
    elif args.command == "scroll":
        request.update(amount=args.amount, x=args.x, y=args.y)
    elif args.command == "type":
        request.update(text=args.text, interval_ms=args.interval_ms)
    elif args.command == "key":
        request.update(key=args.key, ctrl=args.ctrl, alt=args.alt, shift=args.shift)

    try:
        response = _remote_request(args.target, request)
        if args.command == "info":
            response.pop("id", None)
            response.pop("ok", None)
            print(json.dumps(response, indent=2))
        elif args.command in {"screenshot", "observe"}:
            image = base64.b64decode(str(response["image_base64"]), validate=True)
            if args.output == "-":
                sys.stdout.buffer.write(image)
                sys.stdout.buffer.flush()
            else:
                Path(args.output).write_bytes(image)
        return 0
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"sshdesk-remote: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(split_entrypoint())
