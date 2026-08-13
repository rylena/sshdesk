from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.sh"


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

    def test_tailscale_is_official_optional_and_last(self) -> None:
        source = INSTALLER.read_text()
        self.assertIn('TAILSCALE_INSTALL_URL="https://tailscale.com/install.sh"', source)
        self.assertIn("IFS= read -r answer </dev/tty", source)
        self.assertLess(source.rindex("start_openssh\n"), source.rindex("prompt_tailscale\n"))
        self.assertLess(source.rindex("prompt_tailscale\n"), source.rindex("install_tailscale\n"))


if __name__ == "__main__":
    unittest.main()
