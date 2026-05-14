param(
    [string]$RepoPath = "."
)

$ErrorActionPreference = 'Stop'

$root = Resolve-Path -LiteralPath $RepoPath
$doctorPath = Join-Path $root 'scripts\doctor.ps1'

if (-not (Test-Path -LiteralPath $doctorPath)) {
    Write-Error "Missing required path: scripts\doctor.ps1"
    exit 1
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $doctorPath -Mode research -RepoPath $root
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output "Research repo validation passed: $root"
