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


class WindowsInstallerTests(unittest.TestCase):
    def test_windows_bootstrap_downloads_configures_and_prompts_last(self) -> None:
        source = WINDOWS_INSTALLER.read_text()
        self.assertIn('Get-WindowsCapability -Online -Name "OpenSSH.Server', source)
        self.assertIn('"https://github.com/$Repository/archive/refs/heads/$Branch.zip"', source)
        self.assertIn('Read-Host "Install and start Tailscale now? [y/N]"', source)
        self.assertIn("Tailscale.Tailscale", source)
        self.assertIn('Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP"', source)
        self.assertLess(source.index("Restart-Service sshd"), source.index("Read-Host"))


if __name__ == "__main__":
    unittest.main()
