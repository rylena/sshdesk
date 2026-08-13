# Changelog

## 0.3.3

- make package license metadata compatible with older distribution `setuptools`
- build Linux installs in an isolated environment instead of reusing stale system build tools

## 0.3.2

- make the one-line bootstrap detect Linux and macOS hosts
- add a native PowerShell one-line bootstrap for Windows
- configure platform-native OpenSSH where the host permits interactive desktop access
- offer the optional Tailscale install at the end on Linux, macOS, and Windows
- add a cross-platform forced-command entry point that never evaluates remote shell input

## 0.3.1

- add a one-line Linux bootstrap installer
- detect and install missing Python/OpenSSH prerequisites across common distributions
- configure, validate, and start OpenSSH automatically
- optionally install, start, and connect Tailscale after SSHDESK setup

## 0.3.0

- add automatic X11/Wayland/Linux and native macOS/Windows platform selection
- add wlroots, GNOME, and KDE Wayland capture plus ydotool input injection
- add Quartz macOS and SendInput Windows input backends
- add Windows virtual-terminal lifecycle and input handling
- add authenticated `sshdesk-agent` observe/control commands with bounded session mode
- add `sshdesk-remote` for safe computer-use calls through ordinary OpenSSH
- add `sshdesk-split` tmux layout and Kitty graphics passthrough
- allow only the constrained agent grammar through forced-command SSH accounts
- expand tests, platform/security documentation, installers, and the README logo

## 0.2.1

- keep capture fresh at the active rate so input never wakes a stale 2 FPS stream
- wake presentation immediately for keyboard and mouse activity
- bypass rendering for identical fingerprinted frames
- use smaller changed-region tiles to reduce terminal traffic during local motion
- reserve a persistent header for the connected device name
- set the terminal window title to the remote hostname and restore it on exit

## 0.2.0

- target 60 FPS in sharp Kitty-graphics mode with a configurable 1–120 FPS cap
- capture and rendering now overlap through a single-slot latest-frame pump
- prefer a continuously drained, renderer-sized FFmpeg/XCB capture stream
- retain MIT-SHM/OpenCV and Pillow/XCB as automatic fallbacks
- adapt capture work to 30 FPS during light activity and 2 FPS while idle
- expose capture FPS and intentionally dropped intermediate frames in statistics
- cache native XShm/OpenCV views and tune native scaling threads
- add concurrency, resize, frame-drop, and refresh-rate regression tests

## 0.1.0

- initial interactive X11 remote desktop over a normal OpenSSH PTY
- sharp Kitty graphics and universal ANSI renderers
- keyboard, mouse, resize, cursor, frame delta, and statistics support
- forced-command installer, security documentation, and synthetic benchmarks
