param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,

    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Product = "1C",
    [string]$BaselineVersion = "",
    [string]$TargetVersion = "",
    [string]$NextVendorVersion = "",

    [string]$VendorBaseline = "",
    [string]$TargetCf = "",
    [string]$TargetCfe = "",
    [string]$NextVendor = "",

    [string]$RlmVendorBaseline = "",
    [string]$RlmTargetCf = "",
    [string]$RlmTargetCfe = "",
    [string]$RlmNextVendor = "",

    [switch]$InitGit,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Convert-ToForwardSlashPath {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }
    return $Value.Replace('\', '/')
}

function Replace-InTextFile {
    param(
        [string]$Path,
        [hashtable]$Replacements
    )

    $content = Get-Content -Raw -LiteralPath $Path
    foreach ($key in $Replacements.Keys) {
        $content = $content.Replace($key, [string]$Replacements[$key])
    }
    Set-Content -LiteralPath $Path -Value $content -Encoding UTF8
}

$templateRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')
$source = Join-Path $templateRoot 'templates\research-repo'
if (-not (Test-Path -LiteralPath $source)) {
    throw "Template source not found: $source"
}

$targetFullPath = [System.IO.Path]::GetFullPath($TargetPath)
if (Test-Path -LiteralPath $targetFullPath) {
    $existing = Get-ChildItem -Force -LiteralPath $targetFullPath
    if ($existing.Count -gt 0 -and -not $Force) {
        throw "Target directory exists and is not empty. Use -Force only when you intentionally want to merge template files: $targetFullPath"
    }
} else {
    New-Item -ItemType Directory -Force -Path $targetFullPath | Out-Null
}

Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $targetFullPath -Recurse -Force:$Force
}

Copy-Item -LiteralPath (Join-Path $templateRoot 'scripts\doctor.ps1') -Destination (Join-Path $targetFullPath 'scripts\doctor.ps1') -Force

$createdAt = (Get-Date).ToUniversalTime().ToString("o")
$replacements = @{
    "__PROJECT_ID__" = $ProjectId
    "__PRODUCT__" = $Product
    "__BASELINE_VERSION__" = $BaselineVersion
    "__TARGET_VERSION__" = $TargetVersion
    "__NEXT_VENDOR_VERSION__" = $NextVendorVersion
    "__VENDOR_BASELINE__" = Convert-ToForwardSlashPath $VendorBaseline
    "__TARGET_CF__" = Convert-ToForwardSlashPath $TargetCf
    "__TARGET_CFE__" = Convert-ToForwardSlashPath $TargetCfe
    "__NEXT_VENDOR__" = Convert-ToForwardSlashPath $NextVendor
    "__RLM_VENDOR_BASELINE__" = $RlmVendorBaseline
    "__RLM_TARGET_CF__" = $RlmTargetCf
    "__RLM_TARGET_CFE__" = $RlmTargetCfe
    "__RLM_NEXT_VENDOR__" = $RlmNextVendor
    "__CREATED_AT__" = $createdAt
}

$textExtensions = @('.md', '.toml', '.jsonl', '.ps1', '.gitignore')
Get-ChildItem -LiteralPath $targetFullPath -Recurse -File -Force | ForEach-Object {
    if ($textExtensions -contains $_.Extension -or $_.Name -eq '.gitignore') {
        Replace-InTextFile -Path $_.FullName -Replacements $replacements
    }
}

if ($InitGit) {
    git -C $targetFullPath init | Out-Host
}

Write-Output "Created research repo: $targetFullPath"
Write-Output "Project id: $ProjectId"
Write-Output "Next task command:"
Write-Output "powershell -NoProfile -ExecutionPolicy Bypass -File `"$targetFullPath\scripts\queue\Get-NextAnalysisTask.ps1`""
