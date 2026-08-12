# SSH client and terminal compatibility

SSHDESK does not require a custom SSH client. It sends terminal control sequences
through an ordinary interactive SSH PTY and receives the same key and mouse byte
sequences used by terminal applications such as Vim and tmux.

The following client features are detected or handled with fallbacks:

| Feature | Preferred | Fallback |
|---|---|---|
| Color | 24-bit ANSI | xterm 256-color, then basic 16-color |
| Pixels | Kitty graphics RGB tiles | Unicode half-blocks, then colored ASCII spaces |
| Mouse | SGR pixel coordinates | SGR cell coordinates, then legacy X10 |
| Resize | SSH PTY window-change | polled PTY dimensions |
| Keyboard | xterm-compatible CSI sequences | printable UTF-8 and control bytes |

OpenSSH, macOS `ssh`, Windows OpenSSH, PuTTY, and mobile SSH applications can all
carry this data without an SSHDESK-specific extension. Actual rendering depends
on their terminal emulator. Kitty, Ghostty, and WezTerm provide the sharp
real-pixel path. PuTTY and terminals without the Kitty graphics protocol continue
to work through the lower-resolution ANSI path. An SSH library used without a terminal emulator, a
non-interactive `ssh -T` connection, or `TERM=dumb` cannot display an interactive
terminal desktop.

`TERM` is part of a normal SSH PTY request. `COLORTERM` is used when the server
accepts it; otherwise `xterm-256color` conservatively selects 256 colors. A
root-owned `/etc/sshdesk/USER.conf` can override detection:

```text
SSHDESK_RENDER=auto
SSHDESK_COLOR=truecolor
SSHDESK_MOUSE=auto
SSHDESK_UNICODE=auto
SSHDESK_X11_CAPTURE=auto
SSHDESK_MAX_FPS=auto
```

`SSHDESK_RENDER=kitty` makes missing graphics support an explicit error;
`SSHDESK_RENDER=ansi` skips the image capability probe.

Sharp graphics also work inside tmux when tmux supports `allow-passthrough`.
`sshdesk-split` enables it automatically and wraps each Kitty APC for tmux.
Older multiplexer releases may require ANSI mode. The optional split helper is
not required for normal `ssh user@host` sessions.

During a session SSHDESK reserves the first terminal row for the remote device
name and sets the terminal window title to the same hostname. Terminals that
implement the xterm title stack restore the previous title when SSHDESK exits.
