#!/bin/sh
set -eu

REPOSITORY="rylena/sshdesk"
BRANCH="main"
TAILSCALE_INSTALL_URL="https://tailscale.com/install.sh"
TAILSCALE_MAC_URL="https://tailscale.com/download/mac"
HOMEBREW_INSTALL_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
YDOTOOL_VERSION="1.0.4"
YDOTOOL_URL="https://github.com/ReimuNotMoe/ydotool/releases/download/v1.0.4/ydotool-release-ubuntu-latest"
YDOTOOLD_URL="https://github.com/ReimuNotMoe/ydotool/releases/download/v1.0.4/ydotoold-release-ubuntu-latest"
YDOTOOL_SHA256="daa83507a596d6839b7467540382dbdc6e4bf64ebfa4f7d6416e877d9a522c0c"
YDOTOOLD_SHA256="3f14f96308935214c0fb154507360f7632e7deda1935dc2d538259fd9986ed36"
YDOTOOL_SOURCE_URL="https://github.com/ReimuNotMoe/ydotool/archive/refs/tags/v1.0.4.tar.gz"
YDOTOOL_SOURCE_SHA256="ba075a43aa6ead51940e892ecffa4d0b8b40c241e4e2bc4bd9bd26b61fde23bd"
tailscale_choice="ask"
requested_user="${SSHDESK_USER-}"
temporary_directory=""
operating_system="$(uname -s)"

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

Installs SSHDESK for the active Linux or macOS desktop user, configures
OpenSSH, and optionally installs and starts Tailscale at the end.
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

case "${operating_system}" in
    Linux|Darwin) ;;
    *) fail "unsupported operating system: ${operating_system}; use install.ps1 on Windows" ;;
esac
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"
command -v sudo >/dev/null 2>&1 || [ "$(id -u)" -eq 0 ] || fail "sudo is required"

if [ "${operating_system}" = "Darwin" ]; then
    [ -n "${requested_user}" ] || requested_user="$(id -un)"
else
    if [ -z "${requested_user}" ]; then
        requested_user="${SUDO_USER-}"
    fi
    if [ -z "${requested_user}" ] || [ "${requested_user}" = "root" ]; then
        requested_user="${USER-}"
    fi
    if [ -z "${requested_user}" ] || [ "${requested_user}" = "root" ]; then
        requested_user="$(logname 2>/dev/null || true)"
    fi
fi
if [ -z "${requested_user}" ] || [ "${requested_user}" = "root" ]; then
    fail "could not detect the desktop user; rerun with --user USER"
fi
case "${requested_user}" in
    -*|*[!A-Za-z0-9_.-]*|'') fail "invalid desktop user: ${requested_user}" ;;
esac
if [ "${operating_system}" = "Linux" ]; then
    getent passwd "${requested_user}" >/dev/null 2>&1 || \
        fail "user does not exist: ${requested_user}"
else
    id "${requested_user}" >/dev/null 2>&1 || fail "user does not exist: ${requested_user}"
    [ "${requested_user}" = "$(id -un)" ] || \
        fail "run the macOS installer while logged in as ${requested_user}"
fi

if [ "$(id -u)" -eq 0 ]; then
    as_root=""
else
    as_root="sudo"
fi

run_as_desktop_user() {
    if [ "$(id -un)" = "${requested_user}" ]; then
        "$@"
    elif command -v runuser >/dev/null 2>&1; then
        ${as_root} runuser -u "${requested_user}" -- "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -u "${requested_user}" -- "$@"
    else
        fail "could not run a desktop access check as ${requested_user}"
    fi
}

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

install_linux_capture_package() {
    family="$1"
    case "${family}" in
        gnome) executable="gnome-screenshot" ;;
        kde) executable="spectacle" ;;
        wlroots) executable="grim" ;;
        *) fail "unknown Wayland desktop family: ${family}" ;;
    esac
    command -v "${executable}" >/dev/null 2>&1 && return

    say "Installing ${family} Wayland capture support..."
    if command -v apt-get >/dev/null 2>&1; then
        case "${family}" in
            gnome) package="gnome-screenshot" ;;
            kde) package="kde-spectacle" ;;
            wlroots) package="grim" ;;
        esac
        ${as_root} apt-get update
        ${as_root} apt-get install -y "${package}"
    elif command -v dnf >/dev/null 2>&1; then
        case "${family}" in
            gnome) package="gnome-screenshot" ;;
            kde) package="spectacle" ;;
            wlroots) package="grim" ;;
        esac
        ${as_root} dnf install -y "${package}"
    elif command -v yum >/dev/null 2>&1; then
        case "${family}" in
            gnome) package="gnome-screenshot" ;;
            kde) package="spectacle" ;;
            wlroots) package="grim" ;;
        esac
        ${as_root} yum install -y "${package}"
    elif command -v pacman >/dev/null 2>&1; then
        case "${family}" in
            gnome) package="gnome-screenshot" ;;
            kde) package="spectacle" ;;
            wlroots) package="grim" ;;
        esac
        ${as_root} pacman -Sy --needed --noconfirm "${package}"
    elif command -v zypper >/dev/null 2>&1; then
        case "${family}" in
            gnome) package="gnome-screenshot" ;;
            kde) package="spectacle" ;;
            wlroots) package="grim" ;;
        esac
        ${as_root} zypper --non-interactive install "${package}"
    elif command -v apk >/dev/null 2>&1; then
        case "${family}" in
            gnome) package="gnome-screenshot" ;;
            kde) package="spectacle" ;;
            wlroots) package="grim" ;;
        esac
        ${as_root} apk add "${package}"
    else
        fail "install ${executable} for Wayland capture, then rerun this command"
    fi
    command -v "${executable}" >/dev/null 2>&1 || \
        fail "${executable} is unavailable after package installation"
}

install_ydotool_binaries() {
    helper_directory="/usr/local/libexec/sshdesk"
    ydotool_cli="/usr/local/bin/ydotool"
    ydotool_daemon="${helper_directory}/ydotoold"

    if [ "$(uname -m)" = "x86_64" ] && ! command -v apk >/dev/null 2>&1; then
        say "Installing the pinned ydotool ${YDOTOOL_VERSION} Wayland input helper..."
        downloaded_cli="${temporary_directory}/ydotool"
        downloaded_daemon="${temporary_directory}/ydotoold"
        curl -fsSL "${YDOTOOL_URL}" -o "${downloaded_cli}"
        curl -fsSL "${YDOTOOLD_URL}" -o "${downloaded_daemon}"
        command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"
        printf '%s  %s\n' "${YDOTOOL_SHA256}" "${downloaded_cli}" | sha256sum -c -
        printf '%s  %s\n' "${YDOTOOLD_SHA256}" "${downloaded_daemon}" | sha256sum -c -
        chmod 0755 "${downloaded_cli}" "${downloaded_daemon}"
        if "${downloaded_cli}" --help >/dev/null 2>&1 && \
            "${downloaded_daemon}" --help 2>&1 | grep -q -- '--socket-own'; then
            ${as_root} install -d -m 0755 "${helper_directory}"
            ${as_root} install -m 0755 "${downloaded_cli}" "${ydotool_cli}"
            ${as_root} install -m 0755 "${downloaded_daemon}" "${ydotool_daemon}"
            return
        fi
        say "The release binary is incompatible with this system; building ydotool locally..."
    fi

    say "Installing the ydotool build prerequisites..."
    if command -v apt-get >/dev/null 2>&1; then
        ${as_root} apt-get update
        ${as_root} apt-get install -y build-essential cmake
    elif command -v dnf >/dev/null 2>&1; then
        ${as_root} dnf install -y gcc make cmake
    elif command -v yum >/dev/null 2>&1; then
        ${as_root} yum install -y gcc make cmake
    elif command -v pacman >/dev/null 2>&1; then
        ${as_root} pacman -Sy --needed --noconfirm base-devel cmake
    elif command -v zypper >/dev/null 2>&1; then
        ${as_root} zypper --non-interactive install gcc make cmake
    elif command -v apk >/dev/null 2>&1; then
        ${as_root} apk add build-base cmake linux-headers
    else
        fail "install a C compiler, make, and CMake 3.4+, then rerun this command"
    fi

    source_archive="${temporary_directory}/ydotool-${YDOTOOL_VERSION}.tar.gz"
    source_directory="${temporary_directory}/ydotool-source"
    build_directory="${temporary_directory}/ydotool-build"
    curl -fsSL "${YDOTOOL_SOURCE_URL}" -o "${source_archive}"
    command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"
    printf '%s  %s\n' "${YDOTOOL_SOURCE_SHA256}" "${source_archive}" | sha256sum -c -
    mkdir -p "${source_directory}" "${build_directory}"
    tar -xzf "${source_archive}" -C "${source_directory}" --strip-components=1
    (
        cd "${build_directory}"
        cmake -DCMAKE_BUILD_TYPE=Release "${source_directory}"
        cmake --build . --target ydotool ydotoold
    )
    ${as_root} install -d -m 0755 "${helper_directory}"
    ${as_root} install -m 0755 "${build_directory}/ydotool" "${ydotool_cli}"
    ${as_root} install -m 0755 "${build_directory}/ydotoold" "${ydotool_daemon}"
}

configure_ydotool_service() {
    command -v systemctl >/dev/null 2>&1 || \
        fail "automatic Wayland input currently requires systemd"
    if [ ! -c /dev/uinput ]; then
        command -v modprobe >/dev/null 2>&1 || \
            fail "/dev/uinput is missing and modprobe is unavailable"
        ${as_root} modprobe uinput
    fi
    [ -c /dev/uinput ] || fail "the Linux uinput device is unavailable"
    modules_file="${temporary_directory}/sshdesk-uinput.conf"
    printf 'uinput\n' > "${modules_file}"
    ${as_root} install -d -m 0755 /etc/modules-load.d
    ${as_root} install -m 0644 \
        "${modules_file}" /etc/modules-load.d/sshdesk-uinput.conf
    desktop_uid="$(id -u "${requested_user}")"
    desktop_gid="$(id -g "${requested_user}")"
    YDOTOOL_SOCKET="/run/sshdesk-ydotool/socket"
    export YDOTOOL_SOCKET
    service_file="${temporary_directory}/sshdesk-ydotoold.service"
    cat > "${service_file}" <<EOF
[Unit]
Description=SSHDESK Wayland input helper
After=systemd-udevd.service

[Service]
Type=simple
ExecStart=${ydotool_daemon} --socket-path=${YDOTOOL_SOCKET} --socket-perm=0600 --socket-own=${desktop_uid}:${desktop_gid}
Restart=on-failure
RestartSec=1
RuntimeDirectory=sshdesk-ydotool
RuntimeDirectoryMode=0755
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectControlGroups=yes
ProtectKernelLogs=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
LockPersonality=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX
DevicePolicy=closed
DeviceAllow=/dev/uinput rw

[Install]
WantedBy=multi-user.target
EOF
    ${as_root} install -m 0644 \
        "${service_file}" /etc/systemd/system/sshdesk-ydotoold.service
    ${as_root} systemctl daemon-reload
    ${as_root} systemctl enable --now sshdesk-ydotoold.service

    attempts=0
    while [ ! -S "${YDOTOOL_SOCKET}" ] && [ "${attempts}" -lt 50 ]; do
        attempts=$((attempts + 1))
        sleep 0.1
    done
    [ -S "${YDOTOOL_SOCKET}" ] || \
        fail "ydotoold did not create ${YDOTOOL_SOCKET}; check its systemd service"
    if ! run_as_desktop_user env "YDOTOOL_SOCKET=${YDOTOOL_SOCKET}" \
        "${ydotool_cli}" debug >/dev/null 2>&1; then
        fail "${requested_user} cannot connect to the SSHDESK ydotoold socket"
    fi
}

install_linux_desktop_dependencies() {
    [ "${operating_system}" = "Linux" ] || return
    if [ "${XDG_SESSION_TYPE-}" != "wayland" ] && [ -z "${WAYLAND_DISPLAY-}" ]; then
        return
    fi

    desktop_name="$(printf '%s' "${XDG_CURRENT_DESKTOP-}" | tr '[:upper:]' '[:lower:]')"
    case "${desktop_name}" in
        *gnome*|*unity*|*cinnamon*|*budgie*) desktop_family="gnome" ;;
        *kde*|*plasma*) desktop_family="kde" ;;
        *) desktop_family="wlroots" ;;
    esac
    install_linux_capture_package "${desktop_family}"
    install_ydotool_binaries
    configure_ydotool_service
}

find_brew() {
    if command -v brew >/dev/null 2>&1; then
        command -v brew
    elif [ -x /opt/homebrew/bin/brew ]; then
        printf '%s\n' /opt/homebrew/bin/brew
    elif [ -x /usr/local/bin/brew ]; then
        printf '%s\n' /usr/local/bin/brew
    else
        return 1
    fi
}

ensure_homebrew() {
    if brew_binary="$(find_brew 2>/dev/null)"; then
        printf '%s\n' "${brew_binary}"
        return
    fi
    say "Installing Homebrew to provide missing macOS prerequisites..." >&2
    homebrew_script="${temporary_directory}/homebrew-install.sh"
    curl -fsSL "${HOMEBREW_INSTALL_URL}" -o "${homebrew_script}"
    NONINTERACTIVE=1 /bin/bash "${homebrew_script}"
    find_brew || fail "Homebrew installed but its executable could not be found"
}

install_macos_prerequisites() {
    if command -v python3 >/dev/null 2>&1 && \
        python3 -m venv "${temporary_directory}/venv-check" >/dev/null 2>&1; then
        return
    fi
    find "${temporary_directory}/venv-check" -depth -mindepth 1 -delete 2>/dev/null || true
    rmdir "${temporary_directory}/venv-check" 2>/dev/null || true
    brew_binary="$(ensure_homebrew)"
    "${brew_binary}" install python
    brew_prefix="$("${brew_binary}" --prefix)"
    PATH="${brew_prefix}/bin:${PATH}"
    export PATH
    command -v python3 >/dev/null 2>&1 || fail "Python 3 is unavailable after installation"
}

download_project() {
    if [ -n "${SSHDESK_SOURCE_DIR-}" ]; then
        project_directory="${SSHDESK_SOURCE_DIR}"
        [ -f "${project_directory}/pyproject.toml" ] || fail "invalid SSHDESK_SOURCE_DIR"
        return
    fi
    project_directory="${temporary_directory}/sshdesk"
    mkdir -p "${project_directory}"
    archive="${temporary_directory}/sshdesk.tar.gz"
    curl -fsSL \
        "https://github.com/${REPOSITORY}/archive/refs/heads/${BRANCH}.tar.gz" \
        -o "${archive}"
    tar -xzf "${archive}" -C "${project_directory}" --strip-components=1
}

configure_macos_openssh() {
    desktop_home="$(dscl . -read "/Users/${requested_user}" NFSHomeDirectory | \
        awk '{print $2}')"
    [ -n "${desktop_home}" ] || fail "could not find the macOS home directory"
    forced_command="${desktop_home}/.local/bin/sshdesk-forced-command"
    [ -x "${forced_command}" ] || fail "portable forced command was not installed"

    sshd_main="/etc/ssh/sshd_config"
    sshd_directory="/etc/ssh/sshd_config.d"
    sshd_snippet="${sshd_directory}/90-sshdesk-${requested_user}.conf"
    added_include=0
    ${as_root} install -d -m 0755 "${sshd_directory}"
    if ! ${as_root} grep -Eiq \
        '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*' \
        "${sshd_main}"; then
        combined_config="${temporary_directory}/sshd_config-macos"
        printf 'Include /etc/ssh/sshd_config.d/*\n' > "${combined_config}"
        ${as_root} cat "${sshd_main}" >> "${combined_config}"
        ${as_root} cp -p "${sshd_main}" "${sshd_main}.before-sshdesk"
        ${as_root} install -m 0644 "${combined_config}" "${sshd_main}"
        added_include=1
    fi

    snippet="${temporary_directory}/90-sshdesk-macos.conf"
    "${project_directory}/scripts/configure-sshd.sh" \
        "${requested_user}" "${forced_command}" > "${snippet}"
    had_previous=0
    if ${as_root} test -f "${sshd_snippet}"; then
        had_previous=1
        ${as_root} cp -p "${sshd_snippet}" "${temporary_directory}/previous-macos-snippet"
    fi
    ${as_root} install -m 0644 "${snippet}" "${sshd_snippet}"
    if ! ${as_root} /usr/sbin/sshd -t; then
        if [ "${had_previous}" -eq 1 ]; then
            ${as_root} install -m 0644 \
                "${temporary_directory}/previous-macos-snippet" "${sshd_snippet}"
        else
            ${as_root} unlink "${sshd_snippet}"
        fi
        if [ "${added_include}" -eq 1 ]; then
            ${as_root} cp -p "${sshd_main}.before-sshdesk" "${sshd_main}"
        fi
        fail "OpenSSH rejected the macOS configuration; changes were rolled back"
    fi
    ${as_root} /usr/sbin/systemsetup -setremotelogin on >/dev/null || \
        fail "macOS could not enable Remote Login; grant Terminal Full Disk Access and rerun"
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
    if [ "${operating_system}" = "Darwin" ]; then
        if [ ! -d /Applications/Tailscale.app ]; then
            say "Installing the Tailscale macOS app..."
            brew_binary="$(ensure_homebrew)"
            "${brew_binary}" install --cask tailscale
        else
            say "Tailscale is already installed."
        fi
        say "Opening Tailscale to finish its VPN permission and login prompts..."
        open -a Tailscale || open "${TAILSCALE_MAC_URL}"
        return
    fi
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
if [ "${operating_system}" = "Darwin" ]; then
    install_macos_prerequisites
else
    install_prerequisites
    install_linux_desktop_dependencies
    sshd_binary="$(find_sshd)" || fail "OpenSSH server is unavailable after installation"
    command -v ssh-keygen >/dev/null 2>&1 && ${as_root} ssh-keygen -A
fi
download_project

if [ "${operating_system}" = "Darwin" ]; then
    say "Installing SSHDESK for ${requested_user}..."
    SSHDESK_BOOTSTRAP=1 "${project_directory}/scripts/install-macos.sh"
    configure_macos_openssh
    say "SSHDESK is installed and macOS Remote Login is running."
    say "Connect with: ssh ${requested_user}@<server-address>"
    say "Grant Screen Recording and Accessibility permission to the installed Python"
    say "process in System Settings > Privacy & Security before connecting."
    prompt_tailscale
    install_tailscale
    say "Installation complete."
    exit 0
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

say "Verifying graphical capture and input access..."
run_as_desktop_user env \
    "DISPLAY=${display_value}" \
    "XAUTHORITY=${xauthority_value}" \
    "WAYLAND_DISPLAY=${WAYLAND_DISPLAY-}" \
    "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR-}" \
    "XDG_SESSION_TYPE=${XDG_SESSION_TYPE-}" \
    "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP-}" \
    "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS-}" \
    "YDOTOOL_SOCKET=${YDOTOOL_SOCKET-}" \
    /usr/local/bin/sshdesk-server --check

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
