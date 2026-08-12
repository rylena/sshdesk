$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$InstallRoot = Join-Path $env:LOCALAPPDATA "SSHDESK"
$Venv = Join-Path $InstallRoot "venv"
$BinDir = Join-Path $env:LOCALAPPDATA "SSHDESK\bin"

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir | Out-Null
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    py -3 -m venv $Venv
}
& (Join-Path $Venv "Scripts\python.exe") -m pip install --upgrade "${ProjectDir}[fast]"

$Commands = @("sshdesk-server", "sshdesk-agent", "sshdesk-remote")
foreach ($Command in $Commands) {
    $Target = Join-Path $Venv "Scripts\$Command.exe"
    $Wrapper = Join-Path $BinDir "$Command.cmd"
    Set-Content -Encoding Ascii -Path $Wrapper -Value "@`"$Target`" %*"
}

Write-Host "Installed SSHDESK in $InstallRoot."
Write-Host "Add $BinDir to PATH. Windows hosting is experimental and must run"
Write-Host "inside the logged-in interactive desktop session."
