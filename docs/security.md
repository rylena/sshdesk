# Security and permissions

SSHDESK delegates authentication, encryption, host verification, PTY setup, and
connection management to OpenSSH. It adds no credentials and opens no listening
socket.

## Process privileges

Run `sshdesk-server` as the graphical desktop user, never as root. X11 capture
and XTest input require access to the target `DISPLAY` and its Xauthority cookie.
Wayland capture needs the logged-in session's runtime/D-Bus environment, and
ydotool input needs a narrowly configured `ydotoold` with `/dev/uinput` access.
On systemd Linux hosts, the one-line installer isolates that helper in
`sshdesk-ydotoold.service`, exposes a mode-0600 Unix socket owned by the desktop
user, and restricts the service device policy to `/dev/uinput`. The downloaded
x86-64 helper is pinned and SHA-256 verified; incompatible systems build the
same pinned source release locally. The installer also loads the `uinput`
kernel module at boot when it is modular.
Keep session credentials private. If a different dedicated SSH account is used,
granting it access to the graphical session is effectively granting console control.
The MIT-SHM capture segment is created mode 0600 and immediately marked for
automatic removal; it remains attached only for the lifetime of the session.
The preferred FFmpeg backend is launched with a fixed argument vector containing
only validated dimensions, frame rate, and the configured X11 display. Client
data is never passed to FFmpeg or a shell.

The installer places non-secret terminal/display settings in a root-owned,
read-only-to-users mode-0644 file at
`/etc/sshdesk/USER.conf`. The forced-command wrapper parses only fixed keys and
does not evaluate the file as shell code.

When the SSH login and desktop owner differ, the installer creates a per-login
sudoers file. It preserves only fixed display/render environment keys and permits
the argument-free `sshdesk-server` plus the constrained
`sshdesk-agent-ssh` dispatcher as the desktop owner. It never grants a root
command. The dispatcher path lives in a root-owned installation directory.

## OpenSSH boundary

The included configuration forces SSHDESK and disables forwarding, agent
forwarding, tunnels, and user rc files for the matched account. Interactive
desktop connections require a PTY. Non-interactive connections are accepted
only when `SSH_ORIGINAL_COMMAND` begins with the exact `sshdesk-agent` program;
its arguments are parsed into a fixed command grammar and never passed to a
shell. Every other command is rejected. Keep the global OpenSSH
`PermitUserEnvironment no` default; that directive is not portable inside a
`Match` block.

Configure public-key, password, multifactor, source-address, and rate-limit
policy in OpenSSH as usual. Test authentication before applying `ForceCommand`,
and always run `sshd -t` before reloading sshd.

## Runtime input

Terminal and agent input is untrusted even after SSH authentication. The parser caps a
pending escape sequence at 8192 bytes. Terminal dimensions, key actions, mouse
buttons, scroll amounts, and translated desktop coordinates are checked or
bounded before injection. Agent lines, screenshots, text, coordinates, wait
times, button counts, and response sizes are bounded. Terminal bytes and agent
fields are never interpreted as shell commands.

Captured pixels are converted either to numeric ANSI colors or base64-encoded,
zlib-compressed RGB image payloads. Desktop bytes and text are never copied into
terminal commands, preventing captured content from becoming terminal control
sequences. Capability-reported dimensions are bounded before allocation.

On disconnect or failure, SSHDESK releases held synthetic buttons and keys,
closes capture connections/processes, disables mouse reporting, leaves the
alternate screen, and restores POSIX PTY or Windows console attributes.

Anyone who can authenticate to this account can view and control the active
desktop. Treat SSHDESK access as equivalent to physical console access.
