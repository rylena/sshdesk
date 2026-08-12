# SSHDESK

[![Tests](https://github.com/rylena/sshdesk/actions/workflows/test.yml/badge.svg)](https://github.com/rylena/sshdesk/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> SSHDESK is a full interactive remote desktop delivered entirely through an SSH session and displayed directly inside your terminal.

SSHDESK is a Linux/X11 MVP that works with an ordinary interactive SSH client.
After OpenSSH authenticates the user, a forced command captures the active
desktop, renders changed RGB tiles or ANSI cells, parses keyboard and mouse events
from the SSH PTY, and injects them through XTest.

The intended installed experience is exactly:

```bash
ssh desktop@example.com
```

There is no browser, custom client, second login system, VNC/RDP listener, web
server, or additional network port. OpenSSH remains responsible for
authentication, encryption, host keys, PTY allocation, and connection handling.

> [!WARNING]
> Anyone who can authenticate to the SSHDESK account can view and control the
> active graphical desktop. Treat access as equivalent to physical console
> access, and test a second administrative login before forcing SSHDESK on an
> existing account.

## Quick start on Ubuntu or Debian

This MVP targets an active X11 desktop. From the server:

```bash
sudo apt install openssh-server python3 python3-venv python3-pil \
  python3-xlib libxtst6 libxext6 ffmpeg
git clone https://github.com/rylena/sshdesk.git
cd sshdesk

sudo ./scripts/install-server.sh \
  "$USER" "$DISPLAY" "${XAUTHORITY:-$HOME/.Xauthority}"
./scripts/configure-sshd.sh "$USER" |
  sudo tee "/etc/ssh/sshd_config.d/90-sshdesk-$USER.conf"
sudo sshd -t
sudo systemctl reload ssh
```

Then connect from any ordinary interactive SSH client:

```bash
ssh user@server
```

Ghostty, kitty, and WezTerm use the sharp real-pixel renderer. Other terminals
automatically receive the lower-resolution ANSI fallback. Press
`Ctrl+] Ctrl+]` to leave SSHDESK.

## What works

- active X11 desktop capture at its detected resolution
- continuously drained FFmpeg/XCB capture with renderer-sized scaling
- current-frame MIT-SHM and Pillow/XCB capture fallbacks
- real-pixel rendering through the Kitty graphics protocol on kitty, Ghostty,
  and WezTerm, selected by a live capability probe
- automatic ANSI half-block fallback on terminals without image support
- zlib-compressed ~128×128 image tiles with changed-tile retransmission
- full frames, cell deltas, and unchanged-frame suppression
- keyboard, Ctrl/Alt/Shift combinations, arrows, navigation, and F1–F12
- mouse movement, left/right/middle clicks, dragging, and scrolling
- SGR mouse reports with legacy X10 fallback
- dynamic SSH terminal resizing without disconnecting
- aspect-ratio-preserving scaling and letterboxing
- true-color, 256-color, 16-color, and ASCII rendering fallbacks
- independently processed input so slow frame output does not starve controls
- latest-frame scheduling that drops stale work instead of building latency
- adaptive 60 FPS sharp / 30 FPS ANSI active, 30 FPS light, and 2 FPS idle sampling
- cursor-only updates and an optional performance overlay
- terminal restoration and XTest button/key release after disconnects and errors
- a synthetic desktop for headless tests and benchmarks

Current limitations: X11 only; no clipboard or audio; polling is used instead
of XDamage wakeups; Unicode injection depends on the X keyboard map;
and real-pixel graphics require a Kitty-protocol terminal. PipeWire and Wayland
input backends remain future work.

The MVP is implemented in Python for immediate portability and iteration. A
production high-throughput core should eventually move capture, rendering, and
input to Rust without replacing OpenSSH or changing the user experience.

## Server requirements

- Ubuntu/Debian Linux with an active X11 desktop
- OpenSSH server
- Python 3.10 or later
- Pillow, python-xlib, and the XTest extension
- optional NumPy/OpenCV for the fastest native framebuffer scaling
- a normal terminal client with ANSI cursor addressing
- optionally kitty, Ghostty, or WezTerm for sharp real-pixel graphics

Install system dependencies:

```bash
sudo apt install openssh-server python3 python3-venv python3-pil \
  python3-xlib libxtst6 libxext6 ffmpeg
```

For the accelerated scaling path, install the `fast` Python extra in a virtual
environment or otherwise provide `numpy` and `cv2`. SSHDESK automatically falls
back to Pillow scaling if they are unavailable.

The server process must run as the user who owns the graphical desktop so it can
access the correct `DISPLAY` and Xauthority cookie. The SSH login may either be
that user or a dedicated account.

## Install for plain `ssh user@host`

From this repository on the server:

```bash
sudo ./scripts/install-server.sh desktop :0 /home/desktop/.Xauthority
./scripts/configure-sshd.sh desktop |
  sudo tee /etc/ssh/sshd_config.d/90-sshdesk-desktop.conf
sudo sshd -t
sudo systemctl reload ssh
```

To preserve the desktop owner's normal shell, create a dedicated SSH account
and pass the desktop owner as the fourth argument:

```bash
sudo useradd --create-home --shell /bin/bash sshdesk
sudo ./scripts/install-server.sh \
  sshdesk :0 /home/alice/.Xauthority alice
./scripts/configure-sshd.sh sshdesk |
  sudo tee /etc/ssh/sshd_config.d/90-sshdesk.conf
```

In this form the installer creates a narrow sudoers rule: the `sshdesk` account
may run only `/usr/local/bin/sshdesk-server`, without arguments, as `alice`.
OpenSSH still performs all authentication.

Use the actual display value; it may be `:1` rather than `:0`. Before enabling
the `ForceCommand`, verify access from the server:

```bash
sudo -u desktop env DISPLAY=:0 XAUTHORITY=/home/desktop/.Xauthority \
  /usr/local/bin/sshdesk-server --check
```

Ensure SSH authentication for the account works before installing its forced
command. SSHDESK does not create passwords, keys, or another authentication
database. Keep OpenSSH's global `PermitUserEnvironment no` default.

Now connect from an unmodified Linux, macOS, Windows, PuTTY, mobile, or other
interactive SSH client:

```bash
ssh desktop@example.com
```

A normal interactive invocation requests a PTY automatically. Clients configured
to disable PTYs must enable their interactive terminal; `ssh -T` cannot display
SSHDESK.

## Development run

Create a local environment without modifying sshd:

```bash
python3 -m venv .venv --system-site-packages
. .venv/bin/activate
python -m pip install --no-build-isolation -e '.[fast,dev]'

sshdesk-server --capture synthetic --no-input
sshdesk-server --capture x11
```

Exit with `Ctrl+] Ctrl+]`.

## Controls

- Type normally to send keyboard input to the desktop.
- Use the terminal mouse for movement, clicks, drag, and wheel scrolling.
- `Ctrl+S` toggles statistics. Most terminals cannot distinguish
  `Ctrl+Shift+S` from `Ctrl+S`.
- `Ctrl+] Ctrl+]` always exits SSHDESK locally and is not injected remotely.
- Resizing the terminal recalculates the viewport and causes a full redraw.

If automatic terminal detection is too conservative, edit the root-owned
`/etc/sshdesk/USER.conf` created by the installer:

```text
SSHDESK_RENDER=auto
SSHDESK_COLOR=truecolor
SSHDESK_MOUSE=auto
SSHDESK_UNICODE=auto
SSHDESK_X11_CAPTURE=auto
SSHDESK_MAX_FPS=auto
```

See [client compatibility](docs/compatibility.md) for fallbacks.

`SSHDESK_RENDER=auto` probes the connected terminal and chooses real pixels when
supported. Set it to `kitty` to require sharp mode or `ansi` to force the
universal fallback. This setting changes only terminal rendering; the connection
is still ordinary SSH.

`SSHDESK_X11_CAPTURE=auto` prefers a continuously drained FFmpeg/XCB stream,
then current-frame MIT-SHM, then Pillow/XCB. Use `ffmpeg`, `xshm`, or `pillow`
to require a backend for diagnostics.

`SSHDESK_MAX_FPS=auto` targets 60 FPS for sharp terminal pixels and 30 FPS for
ANSI. Set a numeric value from 1 through 120 to override it. Higher rates require
enough CPU, terminal rendering speed, and network bandwidth; SSHDESK always
retains only the newest frame so a slow link cannot create a stale-frame queue.

## Architecture

Capture, rendering, input injection, terminal encoding, session lifecycle, and
statistics are separate modules. OpenSSH launches only `sshdesk-server` and
carries its PTY byte stream over one connection. See
[architecture](docs/architecture.md) and
[security](docs/security.md).

## Documentation

- [Architecture and data flow](docs/architecture.md)
- [Client and terminal compatibility](docs/compatibility.md)
- [Security and permissions](docs/security.md)
- [Benchmark methodology](docs/benchmark.md)
- [Changelog](CHANGELOG.md)

## Test and benchmark

The test suite does not require a graphical desktop:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m unittest discover -s tests -v
```

Check the live X11 backend separately:

```bash
DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" sshdesk-server --check
```

Benchmark exact terminal output bytes:

```bash
sshdesk-bench --duration 60 --columns 100 --rows 30 --color 256
```

See the [benchmark methodology](docs/benchmark.md).

## License

MIT

## Acknowledgements

The sharp rendering path follows the same core idea demonstrated by
[Desktui](https://github.com/mishushakov/desktui): use terminal image pixels and
changed image tiles instead of treating character cells as the display pixels.
