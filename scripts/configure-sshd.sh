#!/bin/sh
set -eu

account="${1:-desktop}"
case "${account}" in
    *[!A-Za-z0-9_.-]*|'')
        echo "invalid account name" >&2
        exit 2
        ;;
esac
cat <<EOF
# Add this block to /etc/ssh/sshd_config after installing
# scripts/sshdesk-forced-command as /usr/local/bin/sshdesk-forced-command.
Match User ${account}
    ForceCommand /usr/local/bin/sshdesk-forced-command
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
