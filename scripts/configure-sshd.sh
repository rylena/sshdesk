#!/bin/sh
set -eu

account="${1:-desktop}"
forced_command="${2:-/usr/local/bin/sshdesk-forced-command}"
case "${account}" in
    *[!A-Za-z0-9_.-]*|'')
        echo "invalid account name" >&2
        exit 2
        ;;
esac
case "${forced_command}" in
    *[!A-Za-z0-9_./:-]*|'')
        echo "invalid forced-command path" >&2
        exit 2
        ;;
esac
cat <<EOF
# Add this block to /etc/ssh/sshd_config after installing
# scripts/sshdesk-forced-command as /usr/local/bin/sshdesk-forced-command.
Match User ${account}
    ForceCommand ${forced_command}
    PermitTTY yes
    DisableForwarding yes
    X11Forwarding no
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitTunnel no
    GatewayPorts no
    PermitUserRC no
Match all
EOF
