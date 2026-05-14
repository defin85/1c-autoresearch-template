param()

$ErrorActionPreference = 'Stop'

$root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')
$errors = New-Object System.Collections.Generic.List[string]

function Add-TemplateError {
    param([string]$Message)
    $script:errors.Add($Message)
}

function Require-Path {
    param([string]$RelativePath)
    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        Add-TemplateError "Missing required path: $RelativePath"
    }
}

@(
    'README.md',
    'AGENTS.md',
    'project.example.toml',
    'docs\agent\index.md',
    'docs\method\1c-autoresearch-process.md',
    'docs\method\queue-design.md',
    'templates\research-repo\project.toml',
    'templates\research-repo\analysis\queue\tasks.jsonl',
    'templates\research-repo\scripts\queue\Get-NextAnalysisTask.ps1',
    'templates\research-repo\scripts\queue\Set-AnalysisTaskStatus.ps1',
    'scripts\bootstrap\New-1cResearchRepo.ps1',
    'scripts\checks\Test-ResearchRepo.ps1'
) | ForEach-Object { Require-Path $_ }

$queue = Join-Path $root 'templates\research-repo\analysis\queue\tasks.jsonl'
if (Test-Path -LiteralPath $queue) {
    Get-Content -LiteralPath $queue | ForEach-Object {
        if ($_.Trim().Length -gt 0) {
            $_ | ConvertFrom-Json | Out-Null
        }
    }
}

if ($errors.Count -gt 0) {
    Write-Error (($errors | ForEach-Object { "- $_" }) -join [Environment]::NewLine)
    exit 1
}

Write-Output "Template validation passed: $root"
