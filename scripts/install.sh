#!/bin/sh
set -eu

REPOSITORY="rylena/sshdesk"
BRANCH="main"
TAILSCALE_INSTALL_URL="https://tailscale.com/install.sh"
tailscale_choice="ask"
requested_user="${SSHDESK_USER-}"
temporary_directory=""

say() {
    printf '%s\n' "$*"
}

fail() {
    say "sshdesk-install: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: install.sh [--user USER] [--tailscale | --no-tailscale]

Installs SSHDESK for the active Linux desktop user, configures OpenSSH, and
optionally installs and starts Tailscale at the end.
EOF
}

cleanup() {
    if [ -n "${temporary_directory}" ] && [ -d "${temporary_directory}" ]; then
        find "${temporary_directory}" -depth -mindepth 1 -delete 2>/dev/null || true
        rmdir "${temporary_directory}" 2>/dev/null || true
    fi
}
trap cleanup EXIT HUP INT TERM

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            [ "$#" -ge 2 ] || fail "--user requires an account name"
            requested_user="$2"
            shift 2
            ;;
        --tailscale)
            tailscale_choice="yes"
            shift
            ;;
        --no-tailscale)
            tailscale_choice="no"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

[ "$(uname -s)" = "Linux" ] || fail "the one-line server installer supports Linux"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"
command -v sudo >/dev/null 2>&1 || [ "$(id -u)" -eq 0 ] || fail "sudo is required"

if [ -z "${requested_user}" ]; then
    requested_user="${SUDO_USER-}"
fi
if [ -z "${requested_user}" ] || [ "${requested_user}" = "root" ]; then
    requested_user="${USER-}"
fi
if [ -z "${requested_user}" ] || [ "${requested_user}" = "root" ]; then
    requested_user="$(logname 2>/dev/null || true)"
fi
if [ -z "${requested_user}" ] || [ "${requested_user}" = "root" ]; then
    fail "could not detect the desktop user; rerun with --user USER"
fi
case "${requested_user}" in
    -*|*[!A-Za-z0-9_.-]*|'') fail "invalid desktop user: ${requested_user}" ;;
esac
getent passwd "${requested_user}" >/dev/null 2>&1 || fail "user does not exist: ${requested_user}"

if [ "$(id -u)" -eq 0 ]; then
    as_root=""
else
    as_root="sudo"
fi

install_prerequisites() {
    need_python=0
    need_sshd=0
    need_venv=0
    command -v python3 >/dev/null 2>&1 || need_python=1
    command -v sshd >/dev/null 2>&1 || [ -x /usr/sbin/sshd ] || need_sshd=1
    if [ "${need_python}" -eq 0 ]; then
        venv_check="${temporary_directory}/venv-check"
        if ! python3 -m venv "${venv_check}" >/dev/null 2>&1; then
            need_venv=1
        fi
        find "${venv_check}" -depth -mindepth 1 -delete 2>/dev/null || true
        rmdir "${venv_check}" 2>/dev/null || true
    fi
    [ "${need_python}" -eq 0 ] && [ "${need_sshd}" -eq 0 ] && \
        [ "${need_venv}" -eq 0 ] && return

    say "Installing required Python and OpenSSH packages..."
    if command -v apt-get >/dev/null 2>&1; then
        ${as_root} apt-get update
        ${as_root} apt-get install -y openssh-server python3 python3-venv
    elif command -v dnf >/dev/null 2>&1; then
        ${as_root} dnf install -y openssh-server python3 python3-pip
    elif command -v yum >/dev/null 2>&1; then
        ${as_root} yum install -y openssh-server python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        ${as_root} pacman -Sy --needed --noconfirm openssh python python-pip
    elif command -v zypper >/dev/null 2>&1; then
        ${as_root} zypper --non-interactive install openssh python3 python3-pip
    elif command -v apk >/dev/null 2>&1; then
        ${as_root} apk add openssh-server python3 py3-pip py3-virtualenv
    else
        fail "install Python 3 with venv and OpenSSH server, then rerun this command"
    fi
}

find_sshd() {
    if command -v sshd >/dev/null 2>&1; then
        command -v sshd
    elif [ -x /usr/sbin/sshd ]; then
        printf '%s\n' /usr/sbin/sshd
    else
        return 1
    fi
}

start_openssh() {
    if command -v systemctl >/dev/null 2>&1; then
        if ${as_root} systemctl enable --now ssh.service >/dev/null 2>&1; then
            ${as_root} systemctl reload ssh.service
            return
        fi
        if ${as_root} systemctl enable --now sshd.service >/dev/null 2>&1; then
            ${as_root} systemctl reload sshd.service
            return
        fi
    fi
    if command -v service >/dev/null 2>&1; then
        if ${as_root} service ssh restart >/dev/null 2>&1; then
            return
        fi
        if ${as_root} service sshd restart >/dev/null 2>&1; then
            return
        fi
    fi
    fail "OpenSSH is configured, but its service could not be started"
}

prompt_tailscale() {
    if [ "${tailscale_choice}" != "ask" ]; then
        return
    fi
    if [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then
        say "No interactive terminal; skipping optional Tailscale installation."
        tailscale_choice="no"
        return
    fi
    printf 'Install and start Tailscale now? [y/N] ' >/dev/tty
    IFS= read -r answer </dev/tty || answer=""
    case "${answer}" in
        y|Y|yes|YES|Yes) tailscale_choice="yes" ;;
        *) tailscale_choice="no" ;;
    esac
}

install_tailscale() {
    [ "${tailscale_choice}" = "yes" ] || return 0
    if ! command -v tailscale >/dev/null 2>&1; then
        say "Installing Tailscale from the official installer..."
        tailscale_script="${temporary_directory}/tailscale-install.sh"
        curl -fsSL "${TAILSCALE_INSTALL_URL}" -o "${tailscale_script}"
        ${as_root} sh "${tailscale_script}"
    else
        say "Tailscale is already installed."
    fi

    if command -v systemctl >/dev/null 2>&1; then
        ${as_root} systemctl enable --now tailscaled.service
    elif command -v service >/dev/null 2>&1; then
        ${as_root} service tailscaled start
    fi

    say "Starting Tailscale login..."
    if [ -r /dev/tty ] && [ -w /dev/tty ]; then
        ${as_root} tailscale up </dev/tty >/dev/tty
    else
        ${as_root} tailscale up
    fi
}

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/sshdesk-install.XXXXXX")"
install_prerequisites
sshd_binary="$(find_sshd)" || fail "OpenSSH server is unavailable after installation"
command -v ssh-keygen >/dev/null 2>&1 && ${as_root} ssh-keygen -A

if [ -n "${SSHDESK_SOURCE_DIR-}" ]; then
    project_directory="${SSHDESK_SOURCE_DIR}"
    [ -f "${project_directory}/pyproject.toml" ] || fail "invalid SSHDESK_SOURCE_DIR"
else
    project_directory="${temporary_directory}/sshdesk"
    mkdir -p "${project_directory}"
    archive="${temporary_directory}/sshdesk.tar.gz"
    curl -fsSL \
        "https://github.com/${REPOSITORY}/archive/refs/heads/${BRANCH}.tar.gz" \
        -o "${archive}"
    tar -xzf "${archive}" -C "${project_directory}" --strip-components=1
fi

desktop_home="$(getent passwd "${requested_user}" | cut -d: -f6)"
display_value="${DISPLAY-}"
if [ -z "${display_value}" ] && [ -z "${WAYLAND_DISPLAY-}" ]; then
    display_value=":0"
fi
xauthority_value="${XAUTHORITY-}"
if [ -z "${xauthority_value}" ]; then
    runtime_directory="${XDG_RUNTIME_DIR-}"
    if [ -n "${runtime_directory}" ] && [ -r "${runtime_directory}/gdm/Xauthority" ]; then
        xauthority_value="${runtime_directory}/gdm/Xauthority"
    else
        xauthority_value="${desktop_home}/.Xauthority"
    fi
fi

say "Installing SSHDESK for ${requested_user}..."
${as_root} env \
    "SSHDESK_BOOTSTRAP=1" \
    "DISPLAY=${display_value}" \
    "XAUTHORITY=${xauthority_value}" \
    "WAYLAND_DISPLAY=${WAYLAND_DISPLAY-}" \
    "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR-}" \
    "XDG_SESSION_TYPE=${XDG_SESSION_TYPE-}" \
    "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP-}" \
    "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS-}" \
    "YDOTOOL_SOCKET=${YDOTOOL_SOCKET-}" \
    "${project_directory}/scripts/install-server.sh" \
    "${requested_user}" "${display_value}" "${xauthority_value}"

sshd_main="/etc/ssh/sshd_config"
sshd_directory="/etc/ssh/sshd_config.d"
sshd_snippet="${sshd_directory}/90-sshdesk-${requested_user}.conf"
added_include=0
${as_root} install -d -m 0755 "${sshd_directory}"
if ! ${as_root} grep -Eiq \
    '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' \
    "${sshd_main}"; then
    say "Enabling the OpenSSH configuration directory..."
    combined_config="${temporary_directory}/sshd_config"
    printf 'Include /etc/ssh/sshd_config.d/*.conf\n' > "${combined_config}"
    ${as_root} cat "${sshd_main}" >> "${combined_config}"
    ${as_root} cp -p "${sshd_main}" "${sshd_main}.before-sshdesk"
    ${as_root} install -m 0600 "${combined_config}" "${sshd_main}"
    added_include=1
fi

snippet="${temporary_directory}/90-sshdesk.conf"
"${project_directory}/scripts/configure-sshd.sh" "${requested_user}" > "${snippet}"
had_previous=0
if ${as_root} test -f "${sshd_snippet}"; then
    had_previous=1
    ${as_root} cp -p "${sshd_snippet}" "${temporary_directory}/previous-sshd-snippet"
fi
${as_root} install -m 0644 "${snippet}" "${sshd_snippet}"
if ! ${as_root} "${sshd_binary}" -t; then
    if [ "${had_previous}" -eq 1 ]; then
        ${as_root} install -m 0644 \
            "${temporary_directory}/previous-sshd-snippet" "${sshd_snippet}"
    else
        ${as_root} unlink "${sshd_snippet}"
    fi
    if [ "${added_include}" -eq 1 ]; then
        ${as_root} cp -p "${sshd_main}.before-sshdesk" "${sshd_main}"
    fi
    fail "OpenSSH rejected the configuration; the SSHDESK snippet was rolled back"
fi
start_openssh

say "SSHDESK is installed and OpenSSH is running."
say "Connect with: ssh ${requested_user}@<server-address>"

# Tailscale is intentionally last so an optional network setup cannot interrupt
# SSHDESK package installation or leave an unvalidated sshd configuration.
prompt_tailscale
install_tailscale

if command -v tailscale >/dev/null 2>&1; then
    tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
    if [ -n "${tailscale_ip}" ]; then
        say "Tailscale SSHDESK address: ssh ${requested_user}@${tailscale_ip}"
    fi
fi
say "Installation complete."
