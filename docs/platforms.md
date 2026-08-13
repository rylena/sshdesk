# Platform support

The SSH client side is terminal data, so Linux, macOS, Windows OpenSSH, PuTTY,
mobile SSH apps, and other interactive clients can display SSHDESK. Sharpness is
determined by the terminal emulator, not the operating system.

The one-line bootstrap supports Linux, macOS, and Windows. That means the
application, native backend, and OpenSSH configuration can be installed on each
OS; the host limitations below still apply after installation.

## Linux host

Linux is the primary supported host platform.

### X11

`X11Capture` detects the desktop size and tries these implementations in order:

1. a continuously drained FFmpeg/XCB process scaled to the current renderer;
2. MIT-SHM current-frame capture, optionally scaled with OpenCV;
3. Pillow/XCB current-frame capture.

`X11Input` injects bounded XTest keyboard and mouse events and releases held
state during cleanup. This path is desktop-environment and distribution neutral
as long as the target is an accessible X11 session.

### Wayland

Wayland deliberately prevents generic applications from reading or controlling
other clients. SSHDESK uses tools already designed for the active compositor:

- wlroots: `grim -c` capture;
- GNOME: `gnome-screenshot` capture;
- KDE Plasma: `spectacle` capture;
- input: `ydotool` connected to `ydotoold` through `/dev/uinput`.

The graphical user's `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, desktop, and D-Bus
environment must reach the forced command. The installer records these when it
is run from that graphical session. The one-line installer detects the desktop,
installs its capture command, and creates `sshdesk-ydotoold.service`. The helper
alone opens `/dev/uinput`; its Unix socket is mode 0600 and owned by the desktop
user. SSHDESK itself stays unprivileged. On an existing installation, rerun the
one-line installer locally from the graphical session to repair missing Wayland
dependencies and revalidate capture.

GNOME 49 removed `org.gnome.Screenshot` from Shell's private screenshot API
allowlist. On GNOME 49 and 50, the installer therefore installs the active,
GNOME-reviewed `allow-gnome-screenshot@siddh.me` compatibility extension and
enables it for the graphical user. A newly copied extension may require one
graphical logout/login before GNOME loads it; rerun the installer afterward so
the real-frame preflight can finish.

Command-based GNOME/KDE capture is functional but slower than X11 or grim. A
PipeWire backend is the intended high-throughput replacement. Compositors that
provide none of the listed capture interfaces need a backend adapter.

## macOS host

`NativeCapture` uses Pillow's CoreGraphics path and `MacOSInput` uses Quartz.
Grant Screen Recording and Accessibility permission to the installed Python
binary. A manually launched server in the logged-in Aqua session is supported;
OpenSSH daemon access depends on macOS TCC/session policy.

## Windows host

`NativeCapture` uses Pillow ImageGrab across virtual screens and
`WindowsInput` uses `SendInput`. The terminal lifecycle enables and restores
Windows virtual-terminal console modes. The server must execute in the logged-in
interactive desktop. Windows OpenSSH normally runs as a service in Session 0,
which can isolate it from that desktop, so forced-command hosting is currently
experimental.
