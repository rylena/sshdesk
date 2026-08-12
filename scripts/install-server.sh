#!/bin/sh
set -eu

usage() {
    echo "Usage: sudo $0 SSH_USER [DISPLAY] [XAUTHORITY] [DESKTOP_USER]" >&2
    echo "Example: sudo $0 sshdesk :0 /home/alice/.Xauthority alice" >&2
    exit 2
}

[ "$#" -ge 1 ] && [ "$#" -le 4 ] || usage
[ "$(id -u)" -eq 0 ] || { echo "run this installer with sudo" >&2; exit 1; }

account="$1"
display="${2:-:0}"
account_home="$(getent passwd "${account}" | cut -d: -f6)"
[ -n "${account_home}" ] || { echo "user does not exist: ${account}" >&2; exit 1; }
xauthority="${3:-${account_home}/.Xauthority}"
run_as="${4:-${account}}"
getent passwd "${run_as}" >/dev/null || { echo "desktop user does not exist: ${run_as}" >&2; exit 1; }

case "${account}${run_as}" in
    *[!A-Za-z0-9_.-]*|'') echo "invalid account name" >&2; exit 2 ;;
esac
case "${display}${xauthority}" in
    *'
'*) echo "display and Xauthority must not contain newlines" >&2; exit 2 ;;
esac

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(dirname -- "${script_dir}")"
install_root="/opt/sshdesk"
venv="${install_root}/venv"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 - <<'PY'
try:
    import PIL
    import Xlib
except ImportError as exc:
    raise SystemExit(
        f"missing {exc.name}; install python3-pil and python3-xlib before SSHDESK"
    )
PY

if ! /usr/bin/sudo -n -u "${run_as}" -- python3 -c 'import cv2, numpy' 2>/dev/null; then
    echo "note: OpenCV/NumPy not found; XShm works but native scaling acceleration is disabled" >&2
fi

install -d -m 0755 "${install_root}" /etc/sshdesk /usr/local/bin /etc/sudoers.d
if [ ! -x "${venv}/bin/python" ]; then
    python3 -m venv --system-site-packages "${venv}"
fi
"${venv}/bin/python" -m pip install --no-build-isolation --upgrade "${project_dir}[fast]"

install -m 0755 "${script_dir}/sshdesk-forced-command" /usr/local/bin/sshdesk-forced-command
ln -sfn "${venv}/bin/sshdesk-server" /usr/local/bin/sshdesk-server
ln -sfn "${venv}/bin/sshdesk-bench" /usr/local/bin/sshdesk-bench
ln -sfn "${venv}/bin/sshdesk-local" /usr/local/bin/sshdesk-local
ln -sfn "${venv}/bin/sshdesk" /usr/local/bin/sshdesk

config="/etc/sshdesk/${account}.conf"
umask 077
{
    printf 'DISPLAY=%s\n' "${display}"
    printf 'XAUTHORITY=%s\n' "${xauthority}"
    printf 'RUN_AS=%s\n' "${run_as}"
    printf 'SSHDESK_RENDER=auto\n'
    printf 'SSHDESK_COLOR=auto\n'
    printf 'SSHDESK_MOUSE=auto\n'
    printf 'SSHDESK_UNICODE=auto\n'
    printf 'SSHDESK_X11_CAPTURE=auto\n'
} > "${config}"
chown root:root "${config}"
chmod 0644 "${config}"

sudoers="/etc/sudoers.d/sshdesk-${account}"
if [ "${run_as}" != "${account}" ]; then
    {
        printf 'Defaults:%s env_keep += "DISPLAY XAUTHORITY SSHDESK_RENDER SSHDESK_COLOR SSHDESK_MOUSE SSHDESK_UNICODE SSHDESK_X11_CAPTURE TERM"\n' "${account}"
        printf '%s ALL=(%s) NOPASSWD: /usr/local/bin/sshdesk-server ""\n' "${account}" "${run_as}"
    } > "${sudoers}"
    chmod 0440 "${sudoers}"
    /usr/sbin/visudo -cf "${sudoers}" >/dev/null
else
    rm -f "${sudoers}"
fi

echo
echo "Installed SSHDESK. Add the following to sshd_config:"
echo
"${script_dir}/configure-sshd.sh" "${account}"
echo
echo "Then validate and reload OpenSSH:"
echo "  sudo sshd -t"
echo "  sudo systemctl reload ssh"
echo
echo "Verify desktop access before enabling ForceCommand:"
echo "  sudo -u ${run_as} DISPLAY='${display}' XAUTHORITY='${xauthority}' sshdesk-server --check"
