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
                                  │   ├── X11Capture
                                  │   │   ├── FFmpeg/XCB
                                  │   │   ├── MIT-SHM fallback
                                  │   │   └── Pillow/XCB fallback
                                  │   ├── GnomeScreenCastCapture
                                  │   │   └── Mutter/PipeWire/GStreamer
                                  │   ├── WaylandCapture
                                  │   └── NativeCapture (macOS/Windows)
                                  ├── Renderer
                                  │   ├── KittyRenderer
                                  │   └── TerminalRenderer (fallback)
                                  ├── InputBackend
                                  │   ├── X11Input
                                  │   ├── MutterInput
                                  │   ├── YdotoolInput
                                  │   ├── MacOSInput
                                  │   └── WindowsInput
                                  └── SessionStats
```

OpenSSH owns authentication, encryption, host keys, PTY allocation, window-size
messages, connection management, and network transport. SSHDESK does not
implement SSH, listen on a port, or define client credentials.

`ScreenCapture`, `Renderer`, and `InputBackend` are independent. Linux selects
X11 or the active Wayland compositor automatically; native macOS and Windows
implementations use the same interfaces. GNOME links one ScreenCast stream to
one RemoteDesktop input session, while other Wayland backends remain isolated
behind the same capture/input abstractions.

The session probes terminal graphics and pixel geometry before starting its input
thread. A capable terminal receives zlib-compressed RGB tiles through the Kitty
graphics protocol; other terminals receive colored half-block cells. The session
keeps rendered state in both modes. A changed frame becomes either a full redraw
or a tile/cell delta; an unchanged frame produces no frame output.
The preferred X11 backend continuously drains an FFmpeg/XCB stream already
scaled to the renderer's exact target. An independent capture worker keeps only
one latest frame, so rendering or SSH backpressure drops intermediate work
instead of queuing stale frames. MIT-SHM with native OpenCV scaling and
Pillow/XCB remain automatic fallbacks. Every backend preserves full-desktop
coordinates for input. A small capture-level fingerprint lets identical frames
reuse the previous rendered state without rebuilding terminal cells or tiles.
GNOME follows the same persistent-stream model: Mutter publishes a PipeWire
node once per session, GStreamer continuously drains and scales it, and input
is sent through the linked Mutter RemoteDesktop object. Terminal resize only
rebuilds the local scaling pipeline, not the compositor session.
Input parsing and X11 injection run in a dedicated thread so slow terminal output
does not starve keyboard or mouse events. The capture rate targets 60 FPS in
sharp mode or 30 FPS in ANSI mode and stays fresh at that rate. Presentation
backs off to 30 FPS during light activity and 2 FPS while idle, but input wakes
it immediately without restarting capture. The active limit can be configured
from 1 through 120 FPS.

There is deliberately no SSHDESK application transport or client binary. An
unmodified SSH client carries one PTY byte stream. Kitty graphics, ANSI/UTF-8,
keyboard reports, and mouse reports are terminal protocols inside that stream.

## Agent path

Agent computer use is an optional, low-frequency control path. OpenSSH invokes
the fixed `sshdesk-agent` command without a PTY. Requests are bounded
newline-delimited JSON because they are infrequent control operations; PNG
observations are base64 encoded in responses. The desktop's interactive frame
path remains the direct terminal stream and never uses JSON.

```text
agent ── sshdesk-remote ── OpenSSH ── sshdesk-agent session
                                      ├── ScreenCapture
                                      └── InputBackend
```

The forced-command dispatcher accepts only the basename `sshdesk-agent`, parses
arguments without a shell, and rejects every other original command. Standard
OpenSSH connection multiplexing can reuse a transport for repeated agent calls.
