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

function Require-SameContent {
    param(
        [string]$ExpectedRelativePath,
        [string]$ActualRelativePath,
        [string]$Message
    )

    $expectedPath = Join-Path $root $ExpectedRelativePath
    $actualPath = Join-Path $root $ActualRelativePath
    if (-not (Test-Path -LiteralPath $expectedPath)) {
        Add-TemplateError "Missing required path: $ExpectedRelativePath"
        return
    }
    if (-not (Test-Path -LiteralPath $actualPath)) {
        Add-TemplateError "Missing required path: $ActualRelativePath"
        return
    }

    $expected = Get-Content -Raw -LiteralPath $expectedPath
    $actual = Get-Content -Raw -LiteralPath $actualPath
    if ($expected -ne $actual) {
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
    'docs\method\evidence-pack-schema.md',
    'docs\method\queue-design.md',
    'templates\research-repo\.gitignore',
    'templates\research-repo\AGENTS.md',
    'templates\research-repo\README.md',
    'templates\research-repo\project.toml',
    'templates\research-repo\.codex\1c-mcp.example.toml',
    'templates\research-repo\docs\agent\index.md',
    'templates\research-repo\docs\agent\repo-map.md',
    'templates\research-repo\docs\agent\verification.md',
    'templates\research-repo\docs\method\1c-autoresearch-process.md',
    'templates\research-repo\docs\method\evidence-pack-schema.md',
    'templates\research-repo\analysis\runs\README.md',
    'templates\research-repo\analysis\cache\AGENTS.md',
    'templates\research-repo\analysis\cache\README.md',
    'templates\research-repo\analysis\features\AGENTS.md',
    'templates\research-repo\analysis\features\README.md',
    'templates\research-repo\analysis\features\_templates\brief.md',
    'templates\research-repo\analysis\features\_templates\evidence.csv',
    'templates\research-repo\analysis\features\_templates\feature-candidates.csv',
    'templates\research-repo\analysis\features\_templates\findings.md',
    'templates\research-repo\analysis\features\_templates\open-questions.md',
    'templates\research-repo\analysis\features\_templates\review.md',
    'templates\research-repo\analysis\queue\README.md',
    'templates\research-repo\analysis\queue\review-checklist.md',
    'templates\research-repo\analysis\queue\runs\README.md',
    'templates\research-repo\analysis\queue\task-schema.md',
    'templates\research-repo\analysis\queue\tasks.jsonl',
    'templates\research-repo\analysis\queue\worker-prompt.md',
    'templates\research-repo\outputs\AGENTS.md',
    'templates\research-repo\outputs\README.md',
    'templates\research-repo\scripts\queue\Claim-NextAnalysisTask.ps1',
    'templates\research-repo\scripts\queue\Get-NextAnalysisTask.ps1',
    'templates\research-repo\scripts\queue\Set-AnalysisTaskStatus.ps1',
    'templates\research-repo\scripts\checks\Test-ResearchRepo.ps1',
    'templates\research-repo\.agents\skills\1c-autoresearch-queue-worker\SKILL.md',
    'scripts\doctor.ps1',
    'scripts\bootstrap\New-1cResearchRepo.ps1',
    'scripts\checks\Test-Doctor.ps1',
    'scripts\checks\Test-ResearchRepo.ps1'
) | ForEach-Object { Require-Path $_ }

Require-NotGitIgnored 'templates/research-repo/analysis/runs/README.md'
Require-NotGitIgnored 'templates/research-repo/analysis/queue/runs/README.md'
Require-Text 'templates\research-repo\.gitignore' '(?m)^\*\.jsonl\.lock$' 'Generated research repo should ignore queue lock files.'
Require-Text 'templates\research-repo\.gitignore' '(?m)^analysis/cache/indexes/\*\*$' 'Generated research repo should ignore generated cache indexes by default.'
Require-Text 'templates\research-repo\.gitignore' '(?m)^analysis/cache/noise/\*\*$' 'Generated research repo should ignore noisy cache diagnostics by default.'
Require-Text 'templates\research-repo\analysis\queue\task-schema.md' '## Optional Fields' 'Queue schema should document optional task fields.'
Require-Text 'templates\research-repo\analysis\queue\tasks.jsonl' 'analysis/features/initial-discovery/evidence\.csv' 'Initial discovery task should expect the canonical evidence pack files.'
Require-Text 'templates\research-repo\docs\agent\verification.md' '1c-mcp\.example\.toml' 'Generated verification docs should explain how to promote the example MCP manifest.'
Require-Text 'docs\agent\verification.md' 'scripts\\doctor\.ps1 -Json -Deep -Strict' 'Template verification runbook should expose the same strict doctor gate used by CI.'
Require-Text 'examples\do-gap-analysis-minimal\README.md' '-RlmTargetCf' 'Minimal example should produce a research repo without empty RLM warnings.'
Require-Text 'templates\research-repo\analysis\queue\worker-prompt.md' 'Run scripts/doctor\.ps1 before updating the task status' 'Queue worker prompt should verify before marking a task complete.'
Require-Text 'templates\research-repo\.agents\skills\1c-autoresearch-queue-worker\SKILL.md' 'Run `scripts\\doctor\.ps1` before updating the selected task status' 'Queue worker skill should verify before marking a task complete.'
Require-Text 'templates\research-repo\analysis\features\_templates\evidence.csv' '^feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes$' 'Evidence CSV template should expose the canonical header.'
Require-Text 'templates\research-repo\analysis\features\_templates\feature-candidates.csv' '^feature_id,title,source_bucket,classification,confidence,summary,next_step$' 'Feature candidate CSV template should expose the canonical header.'
Require-Text 'templates\research-repo\.agents\skills\1c-autoresearch-queue-worker\SKILL.md' 'Claim-NextAnalysisTask\.ps1' 'Queue worker skill should use the atomic claim helper.'
Require-Text 'templates\research-repo\docs\method\1c-autoresearch-process.md' '## Evidence Levels' 'Generated research repo should include the full evidence-level methodology.'
Require-Text 'templates\research-repo\docs\method\evidence-pack-schema.md' 'feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes' 'Generated research repo should include the evidence pack schema.'
Require-Text 'docs\agent\index.md' '\.agents/skills/1c-autoresearch-queue-worker/SKILL\.md' 'Agent index should document the queue-worker skill fallback path.'
Require-SameContent 'docs\method\1c-autoresearch-process.md' 'templates\research-repo\docs\method\1c-autoresearch-process.md' 'Generated research methodology must match the template system-of-record document.'
Require-SameContent 'docs\method\evidence-pack-schema.md' 'templates\research-repo\docs\method\evidence-pack-schema.md' 'Generated evidence pack schema must match the template system-of-record document.'

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
