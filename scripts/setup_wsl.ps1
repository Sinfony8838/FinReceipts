$ErrorActionPreference = 'Stop'
# Official WSL installation without an Ubuntu distribution and without rebooting.
# Run elevated; only installer output is written to the workspace log.
$log = Join-Path $PSScriptRoot '..\runs\wsl-install.log'
& "$env:WINDIR\System32\wsl.exe" --install --no-distribution *> $log
$result = $LASTEXITCODE
Set-Content -LiteralPath (Join-Path $PSScriptRoot '..\runs\wsl-install-exit.txt') -Value $result
exit $result
