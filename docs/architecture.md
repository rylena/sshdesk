# Architecture

SSHDESK is a server-side terminal application launched by OpenSSH as a forced
command. The client is any normal SSH client with an interactive terminal.

```text
normal SSH client and terminal
          │ terminal graphics or ANSI / PTY input
          │ one authenticated SSH session
          ▼
OpenSSH sshd ── ForceCommand ── sshdesk-server
                                  ├── ScreenCapture
                                  │   └── X11Capture (MIT-SHM / Pillow fallback)
                                  ├── Renderer
                                  │   ├── KittyRenderer
                                  │   └── TerminalRenderer (fallback)
                                  ├── InputBackend
                                  │   └── X11Input
                                  └── SessionStats
```

OpenSSH owns authentication, encryption, host keys, PTY allocation, window-size
messages, connection management, and network transport. SSHDESK does not
implement SSH, listen on a port, or define client credentials.

`ScreenCapture`, `Renderer`, and `InputBackend` are independent. A future
PipeWire capture backend and compositor-specific Wayland input backend can be
added without changing the SSH session or terminal renderer.

The session probes terminal graphics and pixel geometry before starting its input
thread. A capable terminal receives zlib-compressed RGB tiles through the Kitty
graphics protocol; other terminals receive colored half-block cells. The session
keeps rendered state in both modes. A changed frame becomes either a full redraw
or a tile/cell delta; an unchanged frame produces no frame output.
The X11 backend captures into MIT-SHM on demand, scales through native OpenCV to
the renderer's exact target, and preserves full-desktop coordinates for input.
Input parsing and X11 injection run in a dedicated thread so slow terminal output
does not starve keyboard or mouse events. The capture rate adapts between 30,
12, and 2 samples per second based on interaction and screen changes.

There is deliberately no SSHDESK application transport or client binary. An
unmodified SSH client carries one PTY byte stream. Kitty graphics, ANSI/UTF-8,
keyboard reports, and mouse reports are terminal protocols inside that stream.
