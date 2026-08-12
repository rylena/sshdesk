#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(dirname -- "${script_dir}")"
install_root="${HOME}/.local/share/sshdesk"
venv="${install_root}/venv"
bin_dir="${HOME}/.local/bin"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
mkdir -p "${install_root}" "${bin_dir}"
[ -x "${venv}/bin/python" ] || python3 -m venv "${venv}"
"${venv}/bin/python" -m pip install --upgrade "${project_dir}[macos,fast]"

for command in sshdesk-server sshdesk-agent sshdesk-agent-ssh sshdesk-remote sshdesk-split; do
    ln -sfn "${venv}/bin/${command}" "${bin_dir}/${command}"
done

echo "Installed SSHDESK in ${install_root}."
echo "Add ${bin_dir} to PATH, then grant Screen Recording and Accessibility"
echo "permission to ${venv}/bin/python in System Settings > Privacy & Security."
