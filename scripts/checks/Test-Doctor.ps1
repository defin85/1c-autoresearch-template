param()

$ErrorActionPreference = 'Stop'

$root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')
$errors = New-Object System.Collections.Generic.List[string]
$tempRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-smoke'
$placeholderRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-placeholder-smoke'
$invalidQueueRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-invalid-queue-smoke'
$mcpWarnRepo = Join-Path $env:TEMP '1c-autoresearch-doctor-mcp-warn-smoke'

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
        }
    }

    if (Test-Path -LiteralPath $tempRepo) {
        Remove-Item -LiteralPath $tempRepo -Recurse -Force
    }
    foreach ($path in @($placeholderRepo, $invalidQueueRepo, $mcpWarnRepo)) {
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

    if (Test-Path -LiteralPath $embeddedDoctor) {
        $researchResult = Invoke-DoctorJson -DoctorPath $embeddedDoctor
        if ($researchResult) {
            Assert-True ($researchResult.repo_kind -eq 'research') "Embedded doctor should detect repo_kind=research"
            Assert-True ($researchResult.status -eq 'ok') "Embedded doctor should report status=ok without -Deep"
        }
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
} finally {
    foreach ($path in @($tempRepo, $placeholderRepo, $invalidQueueRepo, $mcpWarnRepo)) {
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
