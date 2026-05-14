param(
    [string]$RepoPath = "."
)

$ErrorActionPreference = 'Stop'

$root = Resolve-Path -LiteralPath $RepoPath
$errors = New-Object System.Collections.Generic.List[string]

function Add-Error {
    param([string]$Message)
    $script:errors.Add($Message)
}

function Require-Path {
    param([string]$RelativePath)
    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        Add-Error "Missing required path: $RelativePath"
    }
}

@(
    'project.toml',
    'AGENTS.md',
    'README.md',
    'analysis\queue\tasks.jsonl',
    'analysis\queue\task-schema.md',
    'analysis\queue\review-checklist.md',
    'analysis\features\README.md',
    'analysis\cache\README.md',
    'outputs\README.md',
    'scripts\queue\Get-NextAnalysisTask.ps1',
    'scripts\queue\Set-AnalysisTaskStatus.ps1',
    'scripts\checks\Test-ResearchRepo.ps1'
) | ForEach-Object { Require-Path $_ }

$projectToml = Join-Path $root 'project.toml'
if (Test-Path -LiteralPath $projectToml) {
    $content = Get-Content -Raw -LiteralPath $projectToml
    foreach ($section in @('[project]', '[paths]', '[rlm]', '[policy]')) {
        if (-not $content.Contains($section)) {
            Add-Error "project.toml missing section $section"
        }
    }
}

$queue = Join-Path $root 'analysis\queue\tasks.jsonl'
if (Test-Path -LiteralPath $queue) {
    $lineNumber = 0
    Get-Content -LiteralPath $queue | ForEach-Object {
        $lineNumber += 1
        $line = $_.Trim()
        if ($line.Length -eq 0) {
            return
        }
        try {
            $task = $line | ConvertFrom-Json
        } catch {
            Add-Error "Invalid JSONL at tasks.jsonl:$lineNumber - $($_.Exception.Message)"
            return
        }
        foreach ($field in @('id', 'type', 'status', 'priority', 'title', 'feature_id', 'created_at', 'updated_at')) {
            if (-not $task.PSObject.Properties.Name.Contains($field)) {
                Add-Error "Task at line $lineNumber missing field: $field"
            }
        }
    }
}

if ($errors.Count -gt 0) {
    Write-Error (($errors | ForEach-Object { "- $_" }) -join [Environment]::NewLine)
    exit 1
}

Write-Output "Research repo validation passed: $root"
