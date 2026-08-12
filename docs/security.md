# Security and permissions

SSHDESK delegates authentication, encryption, host verification, PTY setup, and
connection management to OpenSSH. It adds no credentials and opens no listening
socket.

## Process privileges

Run `sshdesk-server` as the graphical desktop user, never as root. X11 capture
and XTest input require access to the target `DISPLAY` and its Xauthority cookie.
Keep that cookie private. If a different dedicated SSH account is used, granting
it access to the graphical session is effectively granting it console control.
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
sudoers file. It preserves only the fixed display/render environment and permits
only the argument-free `sshdesk-server` command as the desktop owner. It never
grants a root command.

## OpenSSH boundary

The included configuration forces SSHDESK and disables forwarding, agent
forwarding, tunnels, and user rc files for the matched account. The wrapper
rejects non-empty `SSH_ORIGINAL_COMMAND`, rejects connections without a PTY, and
never passes client data to a shell. Keep the global OpenSSH
`PermitUserEnvironment no` default; that directive is not portable inside a
`Match` block.

Configure public-key, password, multifactor, source-address, and rate-limit
policy in OpenSSH as usual. Test authentication before applying `ForceCommand`,
and always run `sshd -t` before reloading sshd.

## Runtime input

Terminal input is untrusted even after SSH authentication. The parser caps a
pending escape sequence at 8192 bytes. Terminal dimensions, key actions, mouse
buttons, scroll amounts, and translated desktop coordinates are checked or
bounded before injection. XTest is the only input mechanism; terminal bytes are
never interpreted as commands.

Captured pixels are converted either to numeric ANSI colors or base64-encoded,
zlib-compressed RGB image payloads. Desktop bytes and text are never copied into
terminal commands, preventing captured content from becoming terminal control
sequences. Capability-reported dimensions are bounded before allocation.

On disconnect or failure, SSHDESK releases held synthetic buttons and keys,
closes X11 connections, disables mouse reporting, leaves the alternate screen,
and restores PTY attributes.

Anyone who can authenticate to this account can view and control the active
desktop. Treat SSHDESK access as equivalent to physical console access.
