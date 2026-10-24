<##
Data-preserving workaround for Docker Desktop's Windows stale AF_UNIX startup failure.
Only two runtime directories may be renamed, with Docker fully stopped.
No volume, WSL disk, image, settings, or archived runtime directory is deleted.
##>
[CmdletBinding()]
param(
    [string]$LocalData = [Environment]::GetFolderPath('LocalApplicationData'),
    [switch]$RepairRuntime
)
$ErrorActionPreference = 'Stop'
$localRoot = [IO.Path]::GetFullPath($LocalData).TrimEnd('\')
$desktopExe = Join-Path $localRoot 'Programs\DockerDesktop\Docker Desktop.exe'
if (!(Test-Path -LiteralPath $desktopExe -PathType Leaf)) { throw 'Docker Desktop executable not found.' }

if ($RepairRuntime) {
    $running = @(Get-Process -Name 'Docker Desktop','com.docker.backend','com.docker.build','docker-desktop' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) { throw 'Quit Docker Desktop completely before runtime repair.' }
    $runtimePaths = @((Join-Path $localRoot 'Docker\run'), (Join-Path $localRoot 'docker-secrets-engine'))
    $records = @()
    foreach ($candidate in $runtimePaths) {
        $source = [IO.Path]::GetFullPath($candidate)
        $parent = Split-Path -Parent $source
        foreach ($directory in @($localRoot, $parent, $source) | Select-Object -Unique) {
            if (Test-Path -LiteralPath $directory) {
                $item = Get-Item -LiteralPath $directory -Force
                if (!$item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Refusing non-directory or reparse-point parent: $directory"
                }
            }
        }
        if (Test-Path -LiteralPath $source) {
            $leaf = (Split-Path -Leaf $source) + '.preserved-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,6)
            $destination = [IO.Path]::GetFullPath((Join-Path $parent $leaf))
            if ((Split-Path -Parent $destination) -ne $parent -or $source -notin $runtimePaths) {
                throw 'Runtime rename escaped its exact allowed parent.'
            }
            Rename-Item -LiteralPath $source -NewName $leaf
            New-Item -ItemType Directory -Path $source | Out-Null
            $records += [pscustomobject]@{source=$source;preserved=$destination}
        }
    }
    $records | ConvertTo-Json -Depth 3
}
Start-Process -FilePath $desktopExe -WindowStyle Hidden
Write-Output 'Docker Desktop startup requested. Verify docker info before Compose startup.'
