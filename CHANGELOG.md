# Changelog

## Unreleased

- capture macOS desktops with Quartz `CGDisplayCreateImage` instead of Pillow's
  `screencapture` helper, which fails over SSH
- keep macOS mouse coordinates in `CGDisplayPixelsWide`/`High` space
- restore Accessibility checks on PyObjC 12, where `Quartz.AXIsProcessTrusted`
  is no longer exported

## 0.4.5

- adapt display FPS under terminal RTT/write backpressure so slow clients stop accumulating visual lag
- add `SSHDESK_SCALE` / `--scale` to trade detail for smoother output on slower clients
- dynamically lower automatic render scale under client terminal backpressure and recover when it clears
- use fewer, larger Kitty graphics tiles so capable terminals spend less time processing image placements
- pre-scale and fingerprint the Pillow/XCB fallback so static X11 frames avoid unnecessary render work

## 0.4.4

- keep the GNOME cursor out of the PipeWire framebuffer so motion does not encode frames
- send tiny cursor placement updates immediately without waiting for screen capture
- coalesce bursts of terminal mouse-motion reports before desktop input injection
- explicitly close GNOME ScreenCast sessions during disconnect cleanup

## 0.4.3

- stop upscaling smaller remote desktops to large terminal windows
- avoid wasted capture scaling, rendering, diffing, encoding, and terminal decode work

## 0.4.2

- replace large Kitty tile bursts with one atomic, paletted PNG canvas update
- retain small tile deltas while deferring their pixel copies until a tile actually changes
- reduce terminal decode work and SSH bandwidth during scrolling and window movement
- report terminal write time and frame age in the live statistics overlay

## 0.3.5

- restore non-interactive capture on GNOME 49 and 50 with the reviewed compatibility extension
- pin and verify the GNOME extension bundle before installing it as the graphical user
- turn Wayland screenshot-helper timeouts into clean SSHDESK errors instead of tracebacks
- document the GNOME capture permission and one-time extension reload requirement

## 0.3.4

- detect GNOME, KDE Plasma, and wlroots Wayland sessions during installation
- install the matching screenshot dependency automatically
- provision a pinned, checksum-verified ydotool input helper and restricted systemd service
- verify a real desktop frame and ydotool socket access before configuring OpenSSH
- document how to repair older Wayland installations by rerunning the installer

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
