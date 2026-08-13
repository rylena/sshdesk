from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.sh"
WINDOWS_INSTALLER = ROOT / "scripts" / "install.ps1"


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"), "POSIX shell test")
class InstallerTests(unittest.TestCase):
    def test_bootstrap_has_valid_shell_syntax_and_help(self) -> None:
        subprocess.run(["sh", "-n", INSTALLER], check=True)
        result = subprocess.run(
            ["sh", INSTALLER, "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--tailscale | --no-tailscale", result.stdout)
        self.assertIn('Linux|Darwin)', INSTALLER.read_text())

    def test_tailscale_is_official_optional_and_last(self) -> None:
        source = INSTALLER.read_text()
        self.assertIn('TAILSCALE_INSTALL_URL="https://tailscale.com/install.sh"', source)
        self.assertIn("IFS= read -r answer </dev/tty", source)
        self.assertLess(source.rindex("start_openssh\n"), source.rindex("prompt_tailscale\n"))
        self.assertLess(source.rindex("prompt_tailscale\n"), source.rindex("install_tailscale\n"))

    def test_wayland_dependencies_are_installed_and_checked_before_openssh(self) -> None:
        source = INSTALLER.read_text()
        self.assertIn('gnome) executable="gnome-screenshot"', source)
        self.assertIn('kde) executable="spectacle"', source)
        self.assertIn('wlroots) executable="grim"', source)
        self.assertIn('YDOTOOL_VERSION="1.0.4"', source)
        self.assertIn('YDOTOOL_SHA256="daa83507', source)
        self.assertIn('YDOTOOL_SOURCE_SHA256="ba075a43', source)
        self.assertIn('GNOME_SCREENSHOT_EXTENSION_UUID="allow-gnome-screenshot@siddh.me"', source)
        self.assertIn('GNOME_SCREENSHOT_EXTENSION_SHA256="8298e908', source)
        self.assertIn('[ "${shell_major}" -ge 49 ] || return', source)
        self.assertIn("gnome_screenshot_extension_is_active", source)
        self.assertIn("DeviceAllow=/dev/uinput rw", source)
        self.assertIn("/etc/modules-load.d/sshdesk-uinput.conf", source)
        self.assertIn("--socket-perm=0600", source)
        self.assertIn('"${ydotool_cli}" debug', source)
        dependency_install = source.rindex("    install_linux_desktop_dependencies\n")
        application_install = source.rindex('    "${project_directory}/scripts/install-server.sh"')
        desktop_check = source.rindex('say "Verifying graphical capture and input access..."')
        openssh_config = source.rindex('sshd_main="/etc/ssh/sshd_config"')
        self.assertLess(dependency_install, application_install)
        self.assertLess(application_install, desktop_check)
        self.assertLess(desktop_check, openssh_config)


class WindowsInstallerTests(unittest.TestCase):
    def test_windows_bootstrap_downloads_configures_and_prompts_last(self) -> None:
        source = WINDOWS_INSTALLER.read_text()
        self.assertIn('Get-WindowsCapability -Online -Name "OpenSSH.Server', source)
        self.assertIn('"https://github.com/$Repository/archive/refs/heads/$Branch.zip"', source)
        self.assertIn('Read-Host "Install and start Tailscale now? [y/N]"', source)
        self.assertIn("Tailscale.Tailscale", source)
        self.assertIn('Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP"', source)
        self.assertLess(source.index("Restart-Service sshd"), source.index("Read-Host"))


class PackageCompatibilityTests(unittest.TestCase):
    def test_license_uses_pep621_table_for_older_setuptools(self) -> None:
        source = (ROOT / "pyproject.toml").read_text()
        self.assertIn('license = { text = "MIT" }', source)
        self.assertNotIn("license-files", source)

    def test_linux_installer_allows_isolated_build_dependencies(self) -> None:
        source = (ROOT / "scripts" / "install-server.sh").read_text()
        self.assertNotIn("--no-build-isolation", source)


if __name__ == "__main__":
    unittest.main()
