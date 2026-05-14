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

function Require-Text {
    param(
        [string]$RelativePath,
        [string]$Pattern,
        [string]$Message
    )

    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        Add-TemplateError "Missing required path: $RelativePath"
        return
    }

    $content = Get-Content -Raw -LiteralPath $path
    if ($content -notmatch $Pattern) {
        Add-TemplateError $Message
    }
}

function Require-NotGitIgnored {
    param([string]$RelativePath)

    & git -C $root check-ignore -q -- $RelativePath
    if ($LASTEXITCODE -eq 0) {
        Add-TemplateError "Path is unexpectedly git-ignored: $RelativePath"
    }
}

@(
    '.github\workflows\verify.yml',
    'README.md',
    'AGENTS.md',
    'project.example.toml',
    'docs\agent\index.md',
    'docs\agent\repo-map.md',
    'docs\agent\verification.md',
    'docs\method\1c-autoresearch-process.md',
    'docs\method\queue-design.md',
    'templates\research-repo\project.toml',
    'templates\research-repo\docs\agent\index.md',
    'templates\research-repo\docs\agent\repo-map.md',
    'templates\research-repo\docs\agent\verification.md',
    'templates\research-repo\analysis\runs\README.md',
    'templates\research-repo\analysis\queue\runs\README.md',
    'templates\research-repo\analysis\queue\tasks.jsonl',
    'templates\research-repo\scripts\queue\Get-NextAnalysisTask.ps1',
    'templates\research-repo\scripts\queue\Set-AnalysisTaskStatus.ps1',
    'templates\research-repo\.agents\skills\1c-autoresearch-queue-worker\SKILL.md',
    'scripts\doctor.ps1',
    'scripts\bootstrap\New-1cResearchRepo.ps1',
    'scripts\checks\Test-Doctor.ps1',
    'scripts\checks\Test-ResearchRepo.ps1'
) | ForEach-Object { Require-Path $_ }

Require-NotGitIgnored 'templates/research-repo/analysis/runs/README.md'
Require-NotGitIgnored 'templates/research-repo/analysis/queue/runs/README.md'
Require-Text 'templates\research-repo\analysis\queue\task-schema.md' '## Optional Fields' 'Queue schema should document optional task fields.'
Require-Text 'templates\research-repo\docs\method\1c-autoresearch-process.md' '## Evidence Levels' 'Generated research repo should include the full evidence-level methodology.'
Require-Text 'docs\agent\index.md' '\.agents/skills/1c-autoresearch-queue-worker/SKILL\.md' 'Agent index should document the queue-worker skill fallback path.'

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
