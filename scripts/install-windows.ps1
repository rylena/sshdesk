param(
    [string]$PythonExecutable = "",
    [string]$SourceDirectory = "",
    [string]$InstallRootOverride = ""
)

$ErrorActionPreference = "Stop"

$ProjectDir = if ($SourceDirectory) { $SourceDirectory } else { Split-Path -Parent $PSScriptRoot }
$InstallRoot = if ($InstallRootOverride) {
    $InstallRootOverride
} else {
    Join-Path $env:LOCALAPPDATA "SSHDESK"
}
$Venv = Join-Path $InstallRoot "venv"
$BinDir = Join-Path $InstallRoot "bin"
$Python = if ($PythonExecutable) { $PythonExecutable } else { "py" }

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir | Out-Null
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    if ([IO.Path]::GetFileNameWithoutExtension($Python) -eq "py") {
        & $Python -3 -m venv $Venv
    } else {
        & $Python -m venv $Venv
    }
}
& (Join-Path $Venv "Scripts\python.exe") -m pip install --upgrade "${ProjectDir}[fast]"

$Commands = @(
    "sshdesk-server",
    "sshdesk-agent",
    "sshdesk-agent-ssh",
    "sshdesk-forced-command",
    "sshdesk-remote"
)
foreach ($Command in $Commands) {
    $Target = Join-Path $Venv "Scripts\$Command.exe"
    $Wrapper = Join-Path $BinDir "$Command.cmd"
    Set-Content -Encoding Ascii -Path $Wrapper -Value "@`"$Target`" %*"
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $BinDir) {
    $NewPath = if ($UserPath) { "$UserPath;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
}
$env:Path = "$BinDir;$env:Path"

Write-Host "Installed SSHDESK in $InstallRoot."
Write-Host "Add $BinDir to PATH. Windows hosting is experimental and must run"
Write-Host "inside the logged-in interactive desktop session."
