param()

$ErrorActionPreference = 'Stop'

$root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')
$errors = New-Object System.Collections.Generic.List[string]
$tempRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-smoke'
$placeholderRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-placeholder-smoke'
$invalidQueueRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-invalid-queue-smoke'
$invalidEvidenceRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-invalid-evidence-smoke'
$missingEvidenceFileRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-missing-evidence-file-smoke'
$mcpWarnRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-mcp-warn-smoke'
$mcpMismatchRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-mcp-mismatch-smoke'
$rlmMismatchRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-rlm-mismatch-smoke'

function Add-TestError {
    param([string]$Message)
    $script:errors.Add($Message)
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        Add-TestError $Message
    }
}

function Invoke-DoctorJson {
    param(
        [string]$DoctorPath,
        [string[]]$ExtraArgs = @()
    )

    $output = & powershell -NoProfile -ExecutionPolicy Bypass -File $DoctorPath -Json @ExtraArgs
    if ($LASTEXITCODE -ne 0) {
        Add-TestError "doctor exited with code $LASTEXITCODE for $DoctorPath"
        return $null
    }
    return $output | ConvertFrom-Json
}

function Invoke-DoctorJsonAllowFailure {
    param(
        [string]$DoctorPath,
        [string[]]$ExtraArgs = @()
    )

    $output = & powershell -NoProfile -ExecutionPolicy Bypass -File $DoctorPath -Json @ExtraArgs
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Json = ($output | ConvertFrom-Json)
    }
}

function Invoke-ScriptExitCode {
    param(
        [string]$ScriptPath,
        [string[]]$ExtraArgs = @()
    )

    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @ExtraArgs | Out-Null
    return $LASTEXITCODE
}

function Copy-SmokeRepo {
    param([string]$Destination)

    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    Copy-Item -LiteralPath $tempRepo -Destination $Destination -Recurse
}

try {
    $doctorPath = Join-Path $root 'scripts\doctor.ps1'
    Assert-True (Test-Path -LiteralPath $doctorPath) "Missing template doctor script: scripts\doctor.ps1"

    if (Test-Path -LiteralPath $doctorPath) {
        $templateResult = Invoke-DoctorJson -DoctorPath $doctorPath
        if ($templateResult) {
            Assert-True ($templateResult.repo_kind -eq 'template') "Template doctor should detect repo_kind=template"
            Assert-True ($templateResult.status -eq 'ok') "Template doctor should report status=ok"
            $templateChecks = @($templateResult.checks)
            foreach ($requiredCheckId in @(
                'template.required_path.templates/research-repo/AGENTS.md',
                'template.required_path.templates/research-repo/README.md',
                'template.required_path.templates/research-repo/analysis/queue/task-schema.md',
                'template.required_path.templates/research-repo/analysis/queue/review-checklist.md',
                'template.required_path.templates/research-repo/analysis/queue/worker-prompt.md',
                'template.required_path.templates/research-repo/analysis/cache/README.md',
                'template.required_path.templates/research-repo/analysis/features/README.md',
                'template.required_path.templates/research-repo/outputs/README.md',
                'template.required_path.templates/research-repo/docs/method/1c-autoresearch-process.md'
            )) {
                Assert-True (@($templateChecks | Where-Object { $_.id -eq $requiredCheckId }).Count -eq 1) "Template doctor should check required path: $requiredCheckId"
            }
        }
    }

    if (Test-Path -LiteralPath $tempRepo) {
        Remove-Item -LiteralPath $tempRepo -Recurse -Force
    }
    foreach ($path in @($placeholderRepo, $invalidQueueRepo, $invalidEvidenceRepo, $missingEvidenceFileRepo, $mcpWarnRepo, $mcpMismatchRepo, $rlmMismatchRepo)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\bootstrap\New-1cResearchRepo.ps1') `
        -TargetPath $tempRepo `
        -ProjectId smoke `
        -Product '1C Smoke' `
        -BaselineVersion '2.1' `
        -TargetVersion '2.1' `
        -NextVendorVersion '3.0' `
        -VendorBaseline 'E:\Projects\vendor' `
        -TargetCf 'E:\Projects\customer\cf' `
        -TargetCfe 'E:\Projects\customer\cfe' `
        -NextVendor 'E:\Projects\vendor30' `
        -RlmVendorBaseline vendor `
        -RlmTargetCf customer_cf `
        -RlmTargetCfe customer_cfe `
        -RlmNextVendor vendor30 | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Add-TestError "bootstrap failed with code $LASTEXITCODE"
    }

    $embeddedDoctor = Join-Path $tempRepo 'scripts\doctor.ps1'
    Assert-True (Test-Path -LiteralPath $embeddedDoctor) "Bootstrap should install scripts\doctor.ps1 into research repo"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo '.codex\1c-mcp.example.toml')) "Bootstrap should install the example MCP manifest"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo 'analysis\runs\README.md')) "Bootstrap should keep analysis\runs\README.md in research repo"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo 'analysis\cache\AGENTS.md')) "Bootstrap should install analysis cache guardrails"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo 'analysis\features\AGENTS.md')) "Bootstrap should install feature evidence guardrails"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo 'analysis\features\_templates\evidence.csv')) "Bootstrap should install feature evidence templates"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo 'analysis\queue\runs\README.md')) "Bootstrap should keep analysis\queue\runs\README.md in research repo"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo 'outputs\AGENTS.md')) "Bootstrap should install output guardrails"
    Assert-True (Test-Path -LiteralPath (Join-Path $tempRepo 'scripts\queue\Claim-NextAnalysisTask.ps1')) "Bootstrap should install atomic queue claim helper"

    $generatedMethod = Get-Content -Raw -LiteralPath (Join-Path $tempRepo 'docs\method\1c-autoresearch-process.md')
    Assert-True ($generatedMethod -match '## Evidence Levels') "Generated method docs should include evidence levels"
    Assert-True ($generatedMethod -match '## Pipeline') "Generated method docs should include the full analysis pipeline"

    if (Test-Path -LiteralPath $embeddedDoctor) {
        $researchResult = Invoke-DoctorJson -DoctorPath $embeddedDoctor
        if ($researchResult) {
            Assert-True ($researchResult.repo_kind -eq 'research') "Embedded doctor should detect repo_kind=research"
            Assert-True ($researchResult.status -eq 'ok') "Embedded doctor should report status=ok without -Deep"
        }
    }

    $claimScript = Join-Path $tempRepo 'scripts\queue\Claim-NextAnalysisTask.ps1'
    $setStatusScript = Join-Path $tempRepo 'scripts\queue\Set-AnalysisTaskStatus.ps1'
    if ((Test-Path -LiteralPath $claimScript) -and (Test-Path -LiteralPath $setStatusScript)) {
        $claimOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File $claimScript -ClaimedBy worker-a
        Assert-True ($LASTEXITCODE -eq 0) "Claim-NextAnalysisTask should claim the first available task"
        $claimedTask = $claimOutput | ConvertFrom-Json
        Assert-True ($claimedTask.id -eq 'Q-0001') "Claim-NextAnalysisTask should return Q-0001"
        Assert-True ($claimedTask.status -eq 'claimed') "Claim-NextAnalysisTask should mark Q-0001 as claimed"

        $secondClaimOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File $claimScript -ClaimedBy worker-b
        Assert-True ($LASTEXITCODE -eq 0) "Claim-NextAnalysisTask should return cleanly when no task is available"
        $secondClaimText = (($secondClaimOutput | Out-String).Trim())
        Assert-True ($secondClaimText -eq '{}') "Claim-NextAnalysisTask should not claim the same task twice"

        $setDoneExit = Invoke-ScriptExitCode -ScriptPath $setStatusScript -ExtraArgs @('-Id', 'Q-0001', '-Status', 'done', '-ExpectedStatus', 'claimed', '-ResultSummary', 'smoke complete')
        Assert-True ($setDoneExit -eq 0) "Set-AnalysisTaskStatus should allow guarded transitions from the expected status"
    }

    $unresolvedPlaceholders = Get-ChildItem -LiteralPath $tempRepo -Recurse -File -Force |
        Where-Object { @('.md', '.toml', '.jsonl', '.ps1') -contains $_.Extension } |
        Where-Object { $_.FullName -notlike "*\scripts\doctor.ps1" } |
        Select-String -Pattern '__[A-Z][A-Z0-9_]*__' -CaseSensitive
    Assert-True (($unresolvedPlaceholders | Measure-Object).Count -eq 0) "Bootstrap should leave no unresolved template placeholders"

    Copy-SmokeRepo -Destination $placeholderRepo
    Set-Content -LiteralPath (Join-Path $placeholderRepo 'analysis\features\placeholder-test.md') -Value '__UNRESOLVED_PLACEHOLDER__' -Encoding UTF8
    $placeholderResult = Invoke-DoctorJsonAllowFailure -DoctorPath (Join-Path $placeholderRepo 'scripts\doctor.ps1')
    $placeholderChecks = @($placeholderResult.Json.checks)
    Assert-True ($placeholderResult.ExitCode -ne 0) "Doctor should fail when a research repo contains unresolved placeholders"
    Assert-True (@($placeholderChecks | Where-Object { $_.id -eq 'research.unresolved_placeholder' }).Count -gt 0) "Doctor should report research.unresolved_placeholder"

    Copy-SmokeRepo -Destination $invalidQueueRepo
    $queuePath = Join-Path $invalidQueueRepo 'analysis\queue\tasks.jsonl'
    $task = (Get-Content -Raw -LiteralPath $queuePath) | ConvertFrom-Json
    $task.status = 'not-a-status'
    Set-Content -LiteralPath $queuePath -Value ($task | ConvertTo-Json -Depth 20 -Compress) -Encoding UTF8
    $researchCheckExit = Invoke-ScriptExitCode -ScriptPath (Join-Path $root 'scripts\checks\Test-ResearchRepo.ps1') -ExtraArgs @('-RepoPath', $invalidQueueRepo)
    Assert-True ($researchCheckExit -ne 0) "Test-ResearchRepo should fail when doctor finds an invalid queue status"

    Copy-SmokeRepo -Destination $invalidEvidenceRepo
    $invalidEvidenceQueuePath = Join-Path $invalidEvidenceRepo 'analysis\queue\tasks.jsonl'
    $invalidEvidenceTask = (Get-Content -Raw -LiteralPath $invalidEvidenceQueuePath) | ConvertFrom-Json
    $invalidEvidenceTask.status = 'evidence_pack'
    Set-Content -LiteralPath $invalidEvidenceQueuePath -Value ($invalidEvidenceTask | ConvertTo-Json -Depth 20 -Compress) -Encoding UTF8
    $featureFolder = Join-Path $invalidEvidenceRepo 'analysis\features\initial-discovery'
    New-Item -ItemType Directory -Force -Path $featureFolder | Out-Null
    Set-Content -LiteralPath (Join-Path $featureFolder 'findings.md') -Value '# Findings' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $featureFolder 'feature-candidates.csv') -Value 'feature_id,title,source_bucket,classification,confidence,summary,next_step' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $featureFolder 'evidence.csv') -Value 'bad,header' -Encoding UTF8
    $invalidEvidenceResult = Invoke-DoctorJson -DoctorPath (Join-Path $invalidEvidenceRepo 'scripts\doctor.ps1')
    $invalidEvidenceChecks = @($invalidEvidenceResult.checks)
    Assert-True ($invalidEvidenceResult.status -eq 'warn') "Doctor should warn when evidence pack CSV headers do not match the schema"
    Assert-True (@($invalidEvidenceChecks | Where-Object { $_.id -eq 'evidence_pack.invalid_evidence_header' }).Count -gt 0) "Doctor should report evidence_pack.invalid_evidence_header"

    Copy-SmokeRepo -Destination $missingEvidenceFileRepo
    $missingEvidenceQueuePath = Join-Path $missingEvidenceFileRepo 'analysis\queue\tasks.jsonl'
    $missingEvidenceTask = (Get-Content -Raw -LiteralPath $missingEvidenceQueuePath) | ConvertFrom-Json
    $missingEvidenceTask.status = 'evidence_pack'
    if ($missingEvidenceTask.PSObject.Properties.Name.Contains('expected_outputs')) {
        $missingEvidenceTask.PSObject.Properties.Remove('expected_outputs')
    }
    Set-Content -LiteralPath $missingEvidenceQueuePath -Value ($missingEvidenceTask | ConvertTo-Json -Depth 20 -Compress) -Encoding UTF8
    $missingFeatureFolder = Join-Path $missingEvidenceFileRepo 'analysis\features\initial-discovery'
    New-Item -ItemType Directory -Force -Path $missingFeatureFolder | Out-Null
    Set-Content -LiteralPath (Join-Path $missingFeatureFolder 'evidence.csv') -Value 'feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes' -Encoding UTF8
    $missingEvidenceResult = Invoke-DoctorJson -DoctorPath (Join-Path $missingEvidenceFileRepo 'scripts\doctor.ps1')
    $missingEvidenceChecks = @($missingEvidenceResult.checks)
    Assert-True ($missingEvidenceResult.status -eq 'warn') "Doctor should warn when a completed evidence pack is missing required markdown files"
    Assert-True (@($missingEvidenceChecks | Where-Object { $_.id -eq 'evidence_pack.missing_required_file' }).Count -gt 0) "Doctor should report evidence_pack.missing_required_file"

    Copy-SmokeRepo -Destination $mcpWarnRepo
    $projectTomlPath = Join-Path $mcpWarnRepo 'project.toml'
    $projectToml = Get-Content -Raw -LiteralPath $projectTomlPath
    $projectToml = $projectToml -replace '(?m)^enabled = false', 'enabled = true'
    Set-Content -LiteralPath $projectTomlPath -Value $projectToml -Encoding UTF8
    $mcpWarnResult = Invoke-DoctorJson -DoctorPath (Join-Path $mcpWarnRepo 'scripts\doctor.ps1')
    $mcpWarnChecks = @($mcpWarnResult.checks)
    Assert-True ($mcpWarnResult.status -eq 'warn') "Doctor should warn when MCP or web access is enabled but incomplete"
    Assert-True (@($mcpWarnChecks | Where-Object { $_.id -like 'manifest.mcp.*' -and $_.status -eq 'warn' }).Count -gt 0) "Doctor should emit MCP configuration warnings"
    Assert-True (@($mcpWarnChecks | Where-Object { $_.id -like 'manifest.web.*' -and $_.status -eq 'warn' }).Count -gt 0) "Doctor should emit web configuration warnings"

    Copy-SmokeRepo -Destination $mcpMismatchRepo
    $mcpMismatchTomlPath = Join-Path $mcpMismatchRepo 'project.toml'
    $mcpMismatchToml = Get-Content -Raw -LiteralPath $mcpMismatchTomlPath
    $mcpMismatchToml = $mcpMismatchToml -replace '(?ms)\[mcp\]\s*enabled = false\s*server = ""\s*url = ""\s*service_root = "mcp"', "[mcp]`nenabled = true`nserver = `"1c-project`"`nurl = `"http://localhost/project`"`nservice_root = `"mcp`""
    Set-Content -LiteralPath $mcpMismatchTomlPath -Value $mcpMismatchToml -Encoding UTF8
    $localMcpDir = Join-Path $mcpMismatchRepo '.codex'
    New-Item -ItemType Directory -Force -Path $localMcpDir | Out-Null
    Set-Content -LiteralPath (Join-Path $localMcpDir '1c-mcp.toml') -Encoding UTF8 -Value @'
infobase = "project"
mcp_server = "1c-other"
url = "http://localhost/other"
service_root = "mcp"
rlm_project = "project"
'@
    $mcpMismatchResult = Invoke-DoctorJsonAllowFailure -DoctorPath (Join-Path $mcpMismatchRepo 'scripts\doctor.ps1')
    $mcpMismatchChecks = @($mcpMismatchResult.Json.checks)
    Assert-True ($mcpMismatchResult.ExitCode -ne 0) "Doctor should fail when .codex/1c-mcp.toml conflicts with project.toml"
    Assert-True (@($mcpMismatchChecks | Where-Object { $_.id -eq 'manifest.mcp.local_mismatch' }).Count -gt 0) "Doctor should report manifest.mcp.local_mismatch"

    Copy-SmokeRepo -Destination $rlmMismatchRepo
    $rlmMismatchTomlPath = Join-Path $rlmMismatchRepo 'project.toml'
    $rlmMismatchToml = Get-Content -Raw -LiteralPath $rlmMismatchTomlPath
    $rlmMismatchToml = $rlmMismatchToml -replace '(?ms)\[mcp\]\s*enabled = false\s*server = ""\s*url = ""\s*service_root = "mcp"', "[mcp]`nenabled = true`nserver = `"1c-project`"`nurl = `"http://localhost/project`"`nservice_root = `"mcp`""
    Set-Content -LiteralPath $rlmMismatchTomlPath -Value $rlmMismatchToml -Encoding UTF8
    $rlmLocalMcpDir = Join-Path $rlmMismatchRepo '.codex'
    New-Item -ItemType Directory -Force -Path $rlmLocalMcpDir | Out-Null
    Set-Content -LiteralPath (Join-Path $rlmLocalMcpDir '1c-mcp.toml') -Encoding UTF8 -Value @'
infobase = "project"
mcp_server = "1c-project"
url = "http://localhost/project"
service_root = "mcp"
rlm_project = "other_project"
'@
    $rlmMismatchResult = Invoke-DoctorJsonAllowFailure -DoctorPath (Join-Path $rlmMismatchRepo 'scripts\doctor.ps1')
    $rlmMismatchChecks = @($rlmMismatchResult.Json.checks)
    Assert-True ($rlmMismatchResult.ExitCode -ne 0) "Doctor should fail when .codex/1c-mcp.toml rlm_project conflicts with project.toml rlm.target_cf"
    Assert-True (@($rlmMismatchChecks | Where-Object { $_.id -eq 'manifest.local_mcp.rlm_mismatch' }).Count -gt 0) "Doctor should report manifest.local_mcp.rlm_mismatch"
} finally {
    foreach ($path in @($tempRepo, $placeholderRepo, $invalidQueueRepo, $invalidEvidenceRepo, $missingEvidenceFileRepo, $mcpWarnRepo, $mcpMismatchRepo, $rlmMismatchRepo)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

if ($errors.Count -gt 0) {
    Write-Error (($errors | ForEach-Object { "- $_" }) -join [Environment]::NewLine)
    exit 1
}

Write-Output "Doctor smoke tests passed: $root"
