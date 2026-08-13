# SSHDESK

```text
       _____ _____ __  ______  ____________ __ __
      / ___// ___// / / / __ \/ ____/ ___// //_/
      \__ \ \__ \/ /_/ / / / / __/  \__ \/ ,<
     ___/ /___/ / __  / /_/ / /___ ___/ / /| |
    /____//____/_/ /_/_____/_____//____/_/ |_|

        YOUR DESKTOP  //  ONE SSH SESSION  //  ZERO EXTRA PORTS
```

[![Tests](https://github.com/rylena/sshdesk/actions/workflows/test.yml/badge.svg)](https://github.com/rylena/sshdesk/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> SSHDESK is a full interactive remote desktop delivered entirely through an SSH session and displayed directly inside your terminal.

Connect with the SSH client you already have:

```bash
ssh desktop@example.com
```

OpenSSH authenticates the user and launches SSHDESK as a forced command. The
active graphical desktop then appears inside that same terminal. Keyboard,
mouse, resize events, changed pixels, and session cleanup all travel through the
one SSH PTY. There is no browser, custom SSH client, VNC/RDP listener, second
password database, web server, or additional network port.

Kitty, Ghostty, and WezTerm receive sharp real-pixel tiles. Every ordinary ANSI
terminal receives the lower-resolution color-cell renderer, so OpenSSH, PuTTY,
mobile clients, and embedded SSH terminals remain usable.

> [!WARNING]
> Anyone who can authenticate to an SSHDESK account can see and control the
> active graphical session. Treat it like physical console access. Keep a
> second administrative login available while configuring a forced command.

## Features

- full desktop viewing with changed-tile/cell updates and static-frame suppression
- keyboard, Ctrl/Alt/Shift, arrows, navigation keys, and F1–F12
- mouse movement, left/right/middle click, drag, and wheel scrolling
- dynamic terminal resize with aspect-ratio-preserving viewport recalculation
- persistent top bar and terminal title showing the connected device name
- sharp zlib-compressed RGB tiles through Kitty graphics, including tmux passthrough
- true-color, 256-color, 16-color, Unicode, and ASCII fallbacks
- latest-frame scheduling that drops stale work instead of accumulating latency
- 60 FPS sharp / 30 FPS ANSI active targets with adaptive idle presentation
- live FPS, latency, capture, diff, bandwidth, and update instrumentation
- agent-safe screenshot and computer-use commands carried through OpenSSH
- optional tmux side-by-side layout for an agent shell and visual desktop
- terminal restoration and held-input release after disconnects or crashes
- X11, common Wayland desktop, macOS, and Windows backend abstractions

## One-line installation

The bootstrap downloads the same SSHDESK release and selects the native
installer automatically. On Linux or macOS, run this in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/rylena/sshdesk/main/scripts/install.sh | sh
```

On Windows, run this in PowerShell. It requests Administrator permission when
needed:

```powershell
& ([scriptblock]::Create((irm 'https://raw.githubusercontent.com/rylena/sshdesk/main/scripts/install.ps1')))
```

Both one-line entry points detect the OS, install missing Python/OpenSSH
prerequisites, install SSHDESK, validate graphical access and the forced-command
configuration, and start the platform's OpenSSH service. On Wayland, the Linux
installer detects GNOME, KDE Plasma, or wlroots, installs its capture command,
and provisions a checksum-verified `ydotoold` input helper. They support common
Linux distributions, macOS, and Windows 10/11. The installer asks whether to
install and start Tailscale only after SSHDESK and OpenSSH setup succeeds.
Tailscale carries normal OpenSSH over the private tailnet; it does not replace
OpenSSH or add a second SSH authentication mode.

> [!IMPORTANT]
> Cross-platform installation does not remove OS security boundaries. macOS
> still asks for Screen Recording and Accessibility access. Windows OpenSSH
> normally runs in Session 0, so Windows forced-command desktop capture remains
> experimental even though the one-line installer itself is supported. Any OS
> can be the SSH client; Linux remains the recommended SSHDESK host.

> [!NOTE]
> A one-line installer executes downloaded code with administrator permission
> during setup. Review [scripts/install.sh](scripts/install.sh) or
> [scripts/install.ps1](scripts/install.ps1) first if that is not appropriate
> for the machine. On Linux/macOS, use `--user USER` when automatic user
> detection is wrong.

For unattended installs, download the script and use `--tailscale` or
`--no-tailscale`:

```bash
curl -fsSLo /tmp/sshdesk-install.sh \
  https://raw.githubusercontent.com/rylena/sshdesk/main/scripts/install.sh
sh /tmp/sshdesk-install.sh --user alice --no-tailscale
```

Windows PowerShell accepts `-Tailscale` or `-NoTailscale` on the downloaded
script block:

```powershell
& ([scriptblock]::Create((irm 'https://raw.githubusercontent.com/rylena/sshdesk/main/scripts/install.ps1'))) -NoTailscale
```

### Repairing a Wayland installation

If an older installation closes with `Wayland capture needs grim,
gnome-screenshot, or spectacle`, log into that computer's graphical desktop,
open its local terminal, and rerun the Linux/macOS one-line command above. It
installs the correct capture dependency, configures the narrowly scoped input
helper, checks a real frame, and preserves the existing SSHDESK login. Then
retry the ordinary SSH command from the client. GNOME 49 and 50 also require
the GNOME-reviewed [Allow gnome-screenshot extension](https://extensions.gnome.org/extension/9127/allow-gnome-screenshot/),
which the installer downloads, verifies, and enables automatically. If GNOME
has not loaded a newly installed extension yet, log out of the graphical
desktop once, log back in, and rerun the installer.

## Linux host details

### Manual installation

SSHDESK's installer is distribution-independent. It needs Python 3.10+, a
working Python `venv`, OpenSSH server, and the capture/input tools for the active
display stack:

| Linux session | Capture | Input |
|---|---|---|
| X11, any desktop | FFmpeg/XCB, MIT-SHM, or Pillow/XCB | XTest |
| wlroots (Sway, Hyprland, etc.) | `grim` | `ydotool` + `ydotoold` |
| GNOME Wayland | `gnome-screenshot` | `ydotool` + `ydotoold` |
| KDE Plasma Wayland | `spectacle` | `ydotool` + `ydotoold` |

The one-line installer handles these dependencies automatically. For a manual
installation, install the capture command with the distribution package
manager and configure ydotool 1.0.4 or newer. FFmpeg and NumPy/OpenCV are
acceleration paths; SSHDESK falls back when they are absent. Wayland input
requires `ydotoold` to have access to `/dev/uinput`; do not run the whole
SSHDESK server as root.

From the repository on the server:

```bash
sudo ./scripts/install-server.sh \
  "$USER" "$DISPLAY" "${XAUTHORITY:-$HOME/.Xauthority}"

./scripts/configure-sshd.sh "$USER" |
  sudo tee "/etc/ssh/sshd_config.d/90-sshdesk-$USER.conf"
sudo sshd -t
sudo systemctl reload ssh  # some distributions call this service sshd
```

Use the active display value (`:0`, `:1`, and so on). On Wayland, preserve the
logged-in graphical user's session variables when running the installer:

```bash
sudo --preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR,XDG_SESSION_TYPE,\
XDG_CURRENT_DESKTOP,DBUS_SESSION_BUS_ADDRESS,YDOTOOL_SOCKET \
  ./scripts/install-server.sh "$USER" "${DISPLAY:-}" "${XAUTHORITY:-}"
```

This records the compositor, runtime, D-Bus, and ydotool settings. Check the
resulting root-owned `/etc/sshdesk/USER.conf` before enabling the forced command.

Verify backend access first:

```bash
/usr/local/bin/sshdesk-server --check
```

Then connect from another terminal:

```bash
ssh user@server
```

A PTY is required; `ssh -T` cannot display an interactive desktop. Press
`Ctrl+] Ctrl+]` to leave.

### Dedicated SSH account

To preserve a desktop owner's normal SSH shell, use a dedicated login and run
only the tightly scoped server/agent entry points as the graphical user:

```bash
sudo useradd --create-home --shell /bin/bash sshdesk
sudo ./scripts/install-server.sh \
  sshdesk :0 /home/alice/.Xauthority alice
./scripts/configure-sshd.sh sshdesk |
  sudo tee /etc/ssh/sshd_config.d/90-sshdesk.conf
sudo sshd -t && sudo systemctl reload ssh
```

The generated sudoers rule does not grant root. OpenSSH remains the only
authentication system.

## Agent computer use and side-by-side work

The forced-command account accepts a small fixed `sshdesk-agent` command set in
addition to the interactive desktop. It never evaluates a received shell
string. Examples:

```bash
ssh user@server sshdesk-agent info
ssh user@server sshdesk-agent screenshot --max-width 1280 > desktop.png
ssh user@server sshdesk-agent move 900 500
ssh user@server sshdesk-agent click 900 500 --button left
ssh user@server sshdesk-agent scroll -3 900 500
ssh user@server sshdesk-agent type hello
ssh user@server sshdesk-agent key enter
```

For reliable quoting and machine-readable responses, install SSHDESK locally
and use `sshdesk-remote`. It sends bounded newline-delimited JSON to the fixed
remote command:

```bash
sshdesk-remote user@server info
sshdesk-remote user@server screenshot --output desktop.png
sshdesk-remote user@server click 900 500
sshdesk-remote user@server type 'text with spaces'
```

Long-running agents can avoid process setup for every action:

```bash
sshdesk-remote user@server session
{"id":1,"action":"observe","max_width":1280}
{"id":2,"action":"click","x":900,"y":500,"button":"left"}
{"id":3,"action":"type","text":"hello"}
{"id":4,"action":"quit"}
```

To place a local agent shell beside the remote visual desktop, install `tmux`
and run:

```bash
sshdesk-split user@server
```

The right pane is the normal SSHDESK connection; the left pane is available to
your agent or shell and can call `sshdesk-remote`. These optional automation
commands are also ordinary authenticated SSH sessions. Standard OpenSSH
`ControlMaster` configuration can multiplex them over an existing connection;
SSHDESK never opens another service or port.

## Controls and tuning

- type normally to send keyboard input
- use the terminal mouse for movement, clicks, drag, and scrolling
- `Ctrl+S` toggles statistics (most terminals cannot distinguish `Ctrl+Shift+S`)
- `Ctrl+] Ctrl+]` always exits locally and is never injected
- terminal resizing triggers a new viewport and full redraw without disconnecting

The installer writes safe defaults to `/etc/sshdesk/USER.conf`:

```text
SSHDESK_RENDER=auto
SSHDESK_COLOR=auto
SSHDESK_MOUSE=auto
SSHDESK_UNICODE=auto
SSHDESK_X11_CAPTURE=auto
SSHDESK_MAX_FPS=auto
```

`SSHDESK_RENDER=kitty` requires sharp graphics; `ansi` forces the universal
fallback. `SSHDESK_X11_CAPTURE=auto` tries continuously drained FFmpeg/XCB,
then MIT-SHM, then Pillow/XCB. `SSHDESK_MAX_FPS` accepts 1–120.

## macOS and Windows host details

Linux is the primary, fully integrated OpenSSH host. Native Pillow capture plus
Quartz input on macOS and SendInput on Windows are available for development and
manually launched sessions. The repository-local commands below are useful for
development; most users should use the one-line installers above:

```bash
./scripts/install-macos.sh
powershell -ExecutionPolicy Bypass -File scripts/install-windows.ps1
```

macOS requires Screen Recording and Accessibility permission for the installed
Python process. Windows hosting must execute inside the logged-in interactive
desktop; the normal Windows OpenSSH service may be isolated in Session 0, so
forced-command hosting there is experimental. Linux/macOS/Windows terminals are
all supported as clients because the visual protocol remains standard terminal
output over SSH.

See [platform support](docs/platforms.md) for exact backend behavior.

## Development, tests, and benchmark

```bash
python3 -m venv .venv --system-site-packages
. .venv/bin/activate
python -m pip install -e '.[fast,dev]'

sshdesk-server --capture synthetic --no-input
python -m unittest discover -s tests -v
ruff check src tests
```

Benchmark exact rendered terminal bytes:

```bash
sshdesk-bench --duration 60 --columns 100 --rows 30 --color 256
```

Python keeps platform integration and iteration straightforward today. Capture,
rendering, input, session management, and terminal output are separate modules,
so performance-critical pieces can move to Rust later without changing the
OpenSSH user experience.

## Documentation

- [Architecture and data flow](docs/architecture.md)
- [Platform support](docs/platforms.md)
- [Client and terminal compatibility](docs/compatibility.md)
- [Security and permissions](docs/security.md)
- [Benchmark methodology](docs/benchmark.md)
- [Changelog](CHANGELOG.md)

## License

MIT

## Acknowledgements

The sharp renderer builds on the idea demonstrated by
[Desktui](https://github.com/mishushakov/desktui): terminal image pixels and
changed tiles can preserve far more desktop detail than character art.
