from __future__ import annotations

import enum
import os
from collections.abc import Mapping
from dataclasses import dataclass


class ColorMode(enum.Enum):
    TRUECOLOR = "truecolor"
    ANSI256 = "256"
    ANSI16 = "16"


@dataclass(frozen=True, slots=True)
class TerminalCapabilities:
    """Terminal features inferred from standard SSH-propagated environment values.

    TERM is sent by every normal SSH PTY request. COLORTERM is used when the SSH
    server accepts it. Operators can set SSHDESK_COLOR and SSHDESK_MOUSE in the
    root-owned server environment when automatic detection is too conservative.
    """

    term: str
    color: ColorMode
    mouse: bool
    sgr_mouse: bool
    unicode: bool

    @classmethod
    def detect(cls, environment: Mapping[str, str] | None = None) -> TerminalCapabilities:
        env = os.environ if environment is None else environment
        term = env.get("TERM", "").strip().lower()
        if not term or term in {"dumb", "unknown"}:
            raise RuntimeError(
                "SSHDESK requires an interactive ANSI terminal; TERM is missing or dumb"
            )

        override = env.get("SSHDESK_COLOR", "").strip().lower()
        colorterm = env.get("COLORTERM", "").strip().lower()
        if override in {"", "auto"}:
            override = ""
        if override in {"truecolor", "24bit", "24-bit"}:
            color = ColorMode.TRUECOLOR
        elif override in {"256", "256color"}:
            color = ColorMode.ANSI256
        elif override in {"16", "ansi", "basic"}:
            color = ColorMode.ANSI16
        elif override:
            raise RuntimeError("SSHDESK_COLOR must be truecolor, 256, or 16")
        elif colorterm in {"truecolor", "24bit"} or any(
            name in term for name in ("direct", "kitty", "wezterm", "alacritty")
        ):
            color = ColorMode.TRUECOLOR
        elif "256color" in term:
            color = ColorMode.ANSI256
        else:
            color = ColorMode.ANSI16

        mouse_override = env.get("SSHDESK_MOUSE", "auto").strip().lower()
        if mouse_override not in {"auto", "0", "1", "false", "true", "no", "yes"}:
            raise RuntimeError("SSHDESK_MOUSE must be auto, 0, or 1")
        mouse = mouse_override not in {"0", "false", "no"}
        # SGR is understood by modern xterm-compatible emulators. When ignored,
        # terminals normally fall back to the legacy X10 report, which we parse too.
        sgr_mouse = mouse and any(
            name in term
            for name in ("xterm", "screen", "tmux", "rxvt", "kitty", "wezterm", "alacritty")
        )
        unicode_override = env.get("SSHDESK_UNICODE", "auto").strip().lower()
        if unicode_override not in {"auto", "0", "1", "false", "true", "no", "yes"}:
            raise RuntimeError("SSHDESK_UNICODE must be auto, 0, or 1")
        if unicode_override in {"0", "false", "no"}:
            unicode = False
        elif unicode_override in {"1", "true", "yes"}:
            unicode = True
        else:
            unicode = any(
                name in term
                for name in (
                    "xterm",
                    "screen",
                    "tmux",
                    "rxvt",
                    "kitty",
                    "wezterm",
                    "alacritty",
                    "linux",
                )
            )
        return cls(term, color, mouse, sgr_mouse, unicode)
