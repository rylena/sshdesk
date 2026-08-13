param(
    [switch]$Tailscale,
    [switch]$NoTailscale
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repository = "rylena/sshdesk"
$Branch = "main"
$InstallerUrl = "https://raw.githubusercontent.com/rylena/sshdesk/main/scripts/install.ps1"

function Write-Step([string]$Message) {
    Write-Host "SSHDESK: $Message"
}

function Test-Administrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $IsWindows -and $env:OS -ne "Windows_NT") {
    throw "install.ps1 supports Windows; use install.sh on Linux or macOS"
}
if ($Tailscale -and $NoTailscale) {
    throw "choose either -Tailscale or -NoTailscale"
}

if (-not (Test-Administrator)) {
    Write-Step "requesting Administrator permission..."
    $ForwardedSwitches = if ($Tailscale) {
        " -Tailscale"
    } elseif ($NoTailscale) {
        " -NoTailscale"
    } else {
        ""
    }
    $ElevatedCommand = "& ([scriptblock]::Create((Invoke-RestMethod '$InstallerUrl')))" + `
        $ForwardedSwitches
    $Arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        $ElevatedCommand
    )
    Start-Process powershell.exe -Verb RunAs -ArgumentList $Arguments | Out-Null
    return
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$TemporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("sshdesk-install-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $TemporaryDirectory | Out-Null

try {
    $Python = $null
    foreach ($Candidate in @("py.exe", "python.exe")) {
        $Found = Get-Command $Candidate -ErrorAction SilentlyContinue
        if ($Found) {
            if ($Candidate -eq "py.exe") {
                & $Found.Source -3 -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
            } else {
                & $Found.Source -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
            }
            if ($LASTEXITCODE -eq 0) {
                $Python = $Found.Source
                break
            }
        }
    }
    if (-not $Python) {
        $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $Winget) {
            throw "Python 3 is missing and winget is unavailable; install Python 3.10+ and rerun"
        }
        Write-Step "installing Python 3..."
        & $Winget.Source install --id Python.Python.3.13 --exact --source winget `
            --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -ne 0) {
            throw "winget could not install Python"
        }
        $Python = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" `
            -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $Python) {
            $Python = Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" `
                -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
                Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $Python) {
            throw "Python installed, but python.exe could not be found"
        }
    }

    $Capability = Get-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
    if ($Capability.State -ne "Installed") {
        Write-Step "installing the Windows OpenSSH server..."
        Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
    }

    Write-Step "downloading SSHDESK..."
    $Archive = Join-Path $TemporaryDirectory "sshdesk.zip"
    $SourceDirectory = Join-Path $TemporaryDirectory "source"
    Invoke-WebRequest "https://github.com/$Repository/archive/refs/heads/$Branch.zip" `
        -OutFile $Archive -UseBasicParsing
    Expand-Archive -Path $Archive -DestinationPath $SourceDirectory
    $ProjectDirectory = Get-ChildItem $SourceDirectory -Directory | Select-Object -First 1
    if (-not $ProjectDirectory) {
        throw "downloaded SSHDESK archive is empty"
    }

    $InstallRoot = Join-Path $env:ProgramData "SSHDESK"
    Write-Step "installing the application..."
    & (Join-Path $ProjectDirectory.FullName "scripts\install-windows.ps1") `
        -PythonExecutable $Python `
        -SourceDirectory $ProjectDirectory.FullName `
        -InstallRootOverride $InstallRoot

    $Account = $env:USERNAME
    if ($Account -notmatch "^[A-Za-z0-9_.-]+$") {
        throw "the Windows account name cannot be represented safely in sshd_config"
    }
    $SshDirectory = Join-Path $env:ProgramData "ssh"
    $SshConfig = Join-Path $SshDirectory "sshd_config"
    $Sshd = Join-Path $env:SystemRoot "System32\OpenSSH\sshd.exe"
    if (-not (Test-Path $SshConfig) -or -not (Test-Path $Sshd)) {
        throw "Windows OpenSSH installed without its expected configuration files"
    }

    $ForcedCommand = (Join-Path $InstallRoot "bin\sshdesk-forced-command.cmd").Replace("\", "/")
    $BeginMarker = "# BEGIN SSHDESK $Account"
    $EndMarker = "# END SSHDESK $Account"
    $OriginalConfig = [IO.File]::ReadAllText($SshConfig)
    $MarkerPattern = "(?ms)^" + [regex]::Escape($BeginMarker) + ".*?^" + `
        [regex]::Escape($EndMarker) + "\r?\n?"
    $BaseConfig = [regex]::Replace($OriginalConfig, $MarkerPattern, "").TrimEnd()
    $Block = @"
$BeginMarker
Match User $Account
    ForceCommand $ForcedCommand
    PermitTTY yes
    DisableForwarding yes
    X11Forwarding no
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitTunnel no
$EndMarker
"@
    $UpdatedConfig = $BaseConfig + "`r`n`r`n" + $Block.Trim() + "`r`n"
    $BackupConfig = "$SshConfig.before-sshdesk"
    Copy-Item $SshConfig $BackupConfig -Force
    [IO.File]::WriteAllText($SshConfig, $UpdatedConfig, [Text.UTF8Encoding]::new($false))
    & $Sshd -t -f $SshConfig
    if ($LASTEXITCODE -ne 0) {
        Copy-Item $BackupConfig $SshConfig -Force
        throw "OpenSSH rejected the SSHDESK configuration; it was rolled back"
    }

    Set-Service sshd -StartupType Automatic
    if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" `
            -DisplayName "OpenSSH SSH Server (sshd)" -Enabled True `
            -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    }
    if ((Get-Service sshd).Status -eq "Running") {
        Restart-Service sshd
    } else {
        Start-Service sshd
    }
    Write-Step "installed and started OpenSSH. Connect with: ssh $Account@<server-address>"
    Write-Warning "Windows OpenSSH normally runs in Session 0. Desktop capture from a forced command is experimental and must reach the logged-in interactive desktop."

    $InstallTailscale = $Tailscale
    if (-not $Tailscale -and -not $NoTailscale) {
        $Answer = Read-Host "Install and start Tailscale now? [y/N]"
        $InstallTailscale = $Answer -match "^(?i:y|yes)$"
    }
    if ($InstallTailscale) {
        $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $Winget) {
            throw "Tailscale installation needs winget on Windows"
        }
        Write-Step "installing Tailscale..."
        & $Winget.Source install --id Tailscale.Tailscale --exact --source winget `
            --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -ne 0) {
            throw "winget could not install Tailscale"
        }
        Start-Service Tailscale -ErrorAction SilentlyContinue
        $TailscaleExe = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
        if (Test-Path $TailscaleExe) {
            Write-Step "starting Tailscale login..."
            & $TailscaleExe up
        } else {
            Start-Process "https://tailscale.com/download/windows"
        }
    }
    Write-Step "installation complete."
} finally {
    Remove-Item -LiteralPath $TemporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
