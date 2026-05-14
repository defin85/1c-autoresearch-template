param(
    [string]$RepoPath,

    [ValidateSet('auto', 'template', 'research')]
    [string]$Mode = 'auto',

    [switch]$Json,
    [switch]$Deep,
    [switch]$Strict,
    [switch]$NoColor,
    [int]$StaleClaimHours = 12
)

$ErrorActionPreference = 'Stop'

if (-not $RepoPath) {
    $RepoPath = Join-Path $PSScriptRoot '..'
}

$root = Resolve-Path -LiteralPath $RepoPath
$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [string]$Id,
        [ValidateSet('ok', 'warn', 'fail')]
        [string]$Status,
        [string]$Message,
        [object]$Details = $null
    )

    $payload = [ordered]@{
        id = $Id
        status = $Status
        message = $Message
    }
    if ($null -ne $Details) {
        $payload.details = $Details
    }
    [void]$script:checks.Add([pscustomobject]$payload)
}

function Test-RepoPath {
    param([string]$RelativePath)
    return Test-Path -LiteralPath (Join-Path $script:root $RelativePath)
}

function Require-Path {
    param(
        [string]$RelativePath,
        [string]$CheckPrefix
    )

    if (Test-RepoPath $RelativePath) {
        Add-Check "$CheckPrefix.required_path.$($RelativePath.Replace('\', '/'))" ok "Found $RelativePath"
    } else {
        Add-Check "$CheckPrefix.required_path.$($RelativePath.Replace('\', '/'))" fail "Missing required path: $RelativePath"
    }
}

function Read-SimpleToml {
    param([string]$Path)

    $result = @{}
    $section = $null
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) {
            continue
        }
        if ($line -match '^\[(.+)\]$') {
            $section = $Matches[1]
            if (-not $result.ContainsKey($section)) {
                $result[$section] = @{}
            }
            continue
        }
        if ($line -match '^([A-Za-z0-9_\-]+)\s*=\s*(.*)$' -and $section) {
            $key = $Matches[1]
            $value = $Matches[2].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $result[$section][$key] = $value
        }
    }
    return $result
}

function Get-ArrayValue {
    param([object]$Value)
    if ($null -eq $Value) {
        return @()
    }
    if ($Value -is [System.Array]) {
        return @($Value)
    }
    return @($Value)
}

function Get-TomlValue {
    param(
        [hashtable]$Manifest,
        [string]$Section,
        [string]$Key
    )

    if ($Manifest.ContainsKey($Section) -and $Manifest[$Section].ContainsKey($Key)) {
        return [string]$Manifest[$Section][$Key]
    }
    return ''
}

function Test-TomlEnabled {
    param(
        [hashtable]$Manifest,
        [string]$Section
    )

    $value = (Get-TomlValue -Manifest $Manifest -Section $Section -Key 'enabled').Trim().ToLowerInvariant()
    return $value -eq 'true'
}

function Get-RepoKind {
    $isTemplate = Test-RepoPath 'templates\research-repo\project.toml'
    $isResearch = (Test-RepoPath 'project.toml') -and (Test-RepoPath 'analysis\queue\tasks.jsonl')

    if ($script:Mode -ne 'auto') {
        return $script:Mode
    }
    if ($isResearch) {
        return 'research'
    }
    if ($isTemplate) {
        return 'template'
    }
    return 'unknown'
}

function Test-Tool {
    param([string]$Name)
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Add-Check "tool.$Name" ok "Tool is available: $Name"
    } else {
        Add-Check "tool.$Name" warn "Tool is not available on PATH: $Name"
    }
}

function Test-ProjectToml {
    param([string]$RelativePath = 'project.toml')

    $path = Join-Path $script:root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        Add-Check 'manifest.exists' fail "Missing manifest: $RelativePath"
        return $null
    }

    try {
        $manifest = Read-SimpleToml $path
        Add-Check 'manifest.parse' ok "Parsed $RelativePath"
    } catch {
        Add-Check 'manifest.parse' fail "Could not parse ${RelativePath}: $($_.Exception.Message)"
        return $null
    }

    foreach ($section in @('project', 'paths', 'rlm', 'mcp', 'web', 'policy')) {
        if ($manifest.ContainsKey($section)) {
            Add-Check "manifest.section.$section" ok "Found [$section]"
        } else {
            Add-Check "manifest.section.$section" fail "Missing section [$section]"
        }
    }

    if ($manifest.ContainsKey('project')) {
        foreach ($key in @('id', 'product')) {
            if ($manifest['project'].ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($manifest['project'][$key])) {
                Add-Check "manifest.project.$key" ok "Project $key is set"
            } else {
                Add-Check "manifest.project.$key" warn "Project $key is empty"
            }
        }
    }

    if ($manifest.ContainsKey('paths')) {
        foreach ($key in @('vendor_baseline', 'target_cf')) {
            if ($manifest['paths'].ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($manifest['paths'][$key])) {
                Add-Check "manifest.paths.$key" ok "Path $key is set"
            } else {
                Add-Check "manifest.paths.$key" warn "Path $key is empty"
            }
        }

        foreach ($key in @('vendor_baseline', 'target_cf', 'target_cfe', 'next_vendor')) {
            if ($manifest['paths'].ContainsKey($key)) {
                $value = $manifest['paths'][$key]
                if ($script:Deep -and -not [string]::IsNullOrWhiteSpace($value)) {
                    if (Test-Path -LiteralPath $value) {
                        Add-Check "manifest.paths.exists.$key" ok "Path exists: $key"
                    } else {
                        Add-Check "manifest.paths.exists.$key" warn "Path does not exist: $key = $value"
                    }
                }
            }
        }
    }

    if ($manifest.ContainsKey('rlm')) {
        foreach ($key in @('vendor_baseline', 'target_cf', 'target_cfe', 'next_vendor')) {
            if ($manifest['rlm'].ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($manifest['rlm'][$key])) {
                Add-Check "manifest.rlm.$key" ok "RLM project is set: $key"
            } else {
                Add-Check "manifest.rlm.$key" warn "RLM project is empty: $key"
            }
        }
    }

    Test-ManifestAccessPolicy -Manifest $manifest

    return $manifest
}

function Test-ManifestAccessPolicy {
    param([hashtable]$Manifest)

    if ($Manifest.ContainsKey('mcp')) {
        if (Test-TomlEnabled -Manifest $Manifest -Section 'mcp') {
            foreach ($key in @('server', 'url', 'service_root')) {
                $value = Get-TomlValue -Manifest $Manifest -Section 'mcp' -Key $key
                if ([string]::IsNullOrWhiteSpace($value)) {
                    Add-Check "manifest.mcp.$key" warn "MCP is enabled but mcp.$key is empty"
                } else {
                    Add-Check "manifest.mcp.$key" ok "MCP $key is set"
                }
            }
        } else {
            Add-Check 'manifest.mcp.enabled' ok "MCP access is disabled"
        }
    }

    if ($Manifest.ContainsKey('web')) {
        if (Test-TomlEnabled -Manifest $Manifest -Section 'web') {
            foreach ($key in @('url', 'username')) {
                $value = Get-TomlValue -Manifest $Manifest -Section 'web' -Key $key
                if ([string]::IsNullOrWhiteSpace($value)) {
                    Add-Check "manifest.web.$key" warn "Web access is enabled but web.$key is empty"
                } else {
                    Add-Check "manifest.web.$key" ok "Web $key is set"
                }
            }

            $credentialFile = Get-TomlValue -Manifest $Manifest -Section 'web' -Key 'credential_file'
            if ([string]::IsNullOrWhiteSpace($credentialFile)) {
                Add-Check 'manifest.web.credential_file' warn "Web access is enabled without a credential file; default codex/codex credentials are expected"
            } else {
                Add-Check 'manifest.web.credential_file' ok "Web credential file is set"
            }
        } else {
            Add-Check 'manifest.web.enabled' ok "Web access is disabled"
        }
    }
}

function Test-Queue {
    param([string]$RelativePath = 'analysis\queue\tasks.jsonl')

    $path = Join-Path $script:root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        Add-Check 'queue.exists' fail "Missing queue: $RelativePath"
        return
    }

    $tasks = @()
    $lineNumber = 0
    $validStatuses = @('pending', 'claimed', 'evidence_pack', 'drafted', 'needs_review', 'needs_followup', 'blocked', 'done', 'skipped')
    $validTypes = @('discovery', 'deep_dive', 'review', 'migration_map', 'packaging', 'needs_infobase_data')

    Get-Content -LiteralPath $path | ForEach-Object {
        $lineNumber += 1
        $line = $_.Trim()
        if ($line.Length -eq 0) {
            return
        }
        try {
            $task = $line | ConvertFrom-Json
            $task | Add-Member -NotePropertyName '_line' -NotePropertyValue $lineNumber -Force
            $tasks += $task
        } catch {
            Add-Check 'queue.jsonl.parse' fail "Invalid JSONL at ${RelativePath}:$lineNumber - $($_.Exception.Message)"
        }
    }

    if ($tasks.Count -eq 0) {
        Add-Check 'queue.not_empty' warn "Queue has no tasks"
        return
    }
    Add-Check 'queue.not_empty' ok "Queue contains $($tasks.Count) task(s)"

    $ids = @{}
    foreach ($task in $tasks) {
        foreach ($field in @('id', 'type', 'status', 'priority', 'title', 'feature_id', 'created_at', 'updated_at')) {
            if (-not $task.PSObject.Properties.Name.Contains($field)) {
                Add-Check 'queue.required_fields' fail "Task at line $($task._line) missing field: $field"
            }
        }

        if ($task.PSObject.Properties.Name.Contains('id')) {
            if ($ids.ContainsKey($task.id)) {
                Add-Check 'queue.duplicate_id' fail "Duplicate task id: $($task.id)"
            } else {
                $ids[$task.id] = $task
            }
        }

        if ($task.PSObject.Properties.Name.Contains('status') -and $validStatuses -notcontains $task.status) {
            Add-Check 'queue.invalid_status' fail "Task $($task.id) has invalid status: $($task.status)"
        }

        if ($task.PSObject.Properties.Name.Contains('type') -and $validTypes -notcontains $task.type) {
            Add-Check 'queue.invalid_type' fail "Task $($task.id) has invalid type: $($task.type)"
        }
    }

    foreach ($task in $tasks) {
        foreach ($dependency in (Get-ArrayValue $task.dependencies)) {
            if ([string]::IsNullOrWhiteSpace([string]$dependency)) {
                continue
            }
            if (-not $ids.ContainsKey([string]$dependency)) {
                Add-Check 'queue.missing_dependency' fail "Task $($task.id) depends on missing task: $dependency"
            }
        }
    }

    Test-QueueCycles -TasksById $ids

    $staleCutoff = (Get-Date).ToUniversalTime().AddHours(-1 * $script:StaleClaimHours)
    foreach ($task in $tasks) {
        if ($task.status -eq 'claimed' -and $task.PSObject.Properties.Name.Contains('claimed_at')) {
            try {
                $claimedAt = [DateTime]::Parse($task.claimed_at).ToUniversalTime()
                if ($claimedAt -lt $staleCutoff) {
                    Add-Check 'queue.stale_claim' warn "Task $($task.id) has stale claim from $($task.claimed_at)"
                }
            } catch {
                Add-Check 'queue.claimed_at_parse' warn "Task $($task.id) has unparsable claimed_at: $($task.claimed_at)"
            }
        }

        if (@('evidence_pack', 'drafted', 'needs_review', 'done') -contains $task.status) {
            foreach ($output in (Get-ArrayValue $task.expected_outputs)) {
                if ([string]::IsNullOrWhiteSpace([string]$output)) {
                    continue
                }
                if (-not (Test-RepoPath ([string]$output))) {
                    Add-Check 'queue.expected_output_missing' warn "Task $($task.id) expected output is missing: $output"
                }
            }
        }
    }

    if (($checks | Where-Object { $_.id -like 'queue.duplicate_id' }).Count -eq 0) {
        Add-Check 'queue.unique_ids' ok "Task ids are unique"
    }
}

function Test-QueueCycles {
    param([hashtable]$TasksById)

    $visiting = @{}
    $visited = @{}
    $hasCycle = $false

    function Visit {
        param([string]$TaskId, [string[]]$Stack)

        if ($script:visited.ContainsKey($TaskId)) {
            return
        }
        if ($script:visiting.ContainsKey($TaskId)) {
            $script:hasCycle = $true
            Add-Check 'queue.dependency_cycle' fail "Dependency cycle includes $TaskId"
            return
        }
        if (-not $script:TasksById.ContainsKey($TaskId)) {
            return
        }

        $script:visiting[$TaskId] = $true
        $task = $script:TasksById[$TaskId]
        foreach ($dependency in (Get-ArrayValue $task.dependencies)) {
            if (-not [string]::IsNullOrWhiteSpace([string]$dependency)) {
                Visit -TaskId ([string]$dependency) -Stack ($Stack + $TaskId)
            }
        }
        $script:visiting.Remove($TaskId)
        $script:visited[$TaskId] = $true
    }

    $script:TasksById = $TasksById
    $script:visiting = $visiting
    $script:visited = $visited
    $script:hasCycle = $false

    foreach ($taskId in $TasksById.Keys) {
        Visit -TaskId $taskId -Stack @()
    }

    if (-not $script:hasCycle) {
        Add-Check 'queue.no_dependency_cycles' ok "No dependency cycles detected"
    }
}

function Test-UnresolvedPlaceholders {
    $rootPath = ([string]$script:root).TrimEnd('\', '/')
    $textExtensions = @('.md', '.toml', '.jsonl', '.ps1', '.txt', '.csv')
    $found = $false

    Get-ChildItem -LiteralPath $script:root -Recurse -File -Force | Where-Object {
        ($textExtensions -contains $_.Extension) -or $_.Name -eq '.gitignore'
    } | ForEach-Object {
        $relativePath = $_.FullName.Substring($rootPath.Length).TrimStart('\', '/')
        if ($relativePath -eq 'scripts\doctor.ps1') {
            return
        }

        $matches = Select-String -LiteralPath $_.FullName -Pattern '__[A-Z][A-Z0-9_]*__' -AllMatches -CaseSensitive
        foreach ($match in $matches) {
            $found = $true
            Add-Check 'research.unresolved_placeholder' fail "Unresolved template placeholder in ${relativePath}:$($match.LineNumber)"
        }
    }

    if (-not $found) {
        Add-Check 'research.unresolved_placeholders' ok "No unresolved template placeholders found"
    }
}

function Test-TemplateRepo {
    foreach ($path in @(
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
        'templates\research-repo\analysis\queue\tasks.jsonl',
        'templates\research-repo\scripts\queue\Get-NextAnalysisTask.ps1',
        'templates\research-repo\scripts\queue\Set-AnalysisTaskStatus.ps1',
        'templates\research-repo\.agents\skills\1c-autoresearch-queue-worker\SKILL.md',
        'scripts\doctor.ps1',
        'scripts\bootstrap\New-1cResearchRepo.ps1',
        'scripts\checks\Test-Template.ps1',
        'scripts\checks\Test-Doctor.ps1',
        'scripts\checks\Test-ResearchRepo.ps1'
    )) {
        Require-Path $path 'template'
    }

    Test-Queue 'templates\research-repo\analysis\queue\tasks.jsonl'

    foreach ($customerPath in @('cf', 'cfe', 'analysis\cache', 'outputs\do21-functional-customizations')) {
        if (Test-RepoPath $customerPath) {
            Add-Check 'template.customer_artifact' warn "Template contains project-specific looking path: $customerPath"
        }
    }
}

function Test-ResearchRepo {
    foreach ($path in @(
        'project.toml',
        'AGENTS.md',
        'README.md',
        'docs\agent\index.md',
        'docs\agent\repo-map.md',
        'docs\agent\verification.md',
        'analysis\queue\tasks.jsonl',
        'analysis\queue\task-schema.md',
        'analysis\queue\review-checklist.md',
        'analysis\features\README.md',
        'analysis\cache\README.md',
        'outputs\README.md',
        '.agents\skills\1c-autoresearch-queue-worker\SKILL.md',
        'scripts\doctor.ps1',
        'scripts\queue\Get-NextAnalysisTask.ps1',
        'scripts\queue\Set-AnalysisTaskStatus.ps1',
        'scripts\checks\Test-ResearchRepo.ps1'
    )) {
        Require-Path $path 'research'
    }

    Test-ProjectToml 'project.toml' | Out-Null
    Test-Queue 'analysis\queue\tasks.jsonl'
    Test-UnresolvedPlaceholders
}

$repoKind = Get-RepoKind
if ($repoKind -eq 'unknown') {
    Add-Check 'repo.detect' fail "Could not detect repo kind. Expected template or research repo contract."
} else {
    Add-Check 'repo.detect' ok "Detected repo kind: $repoKind"
}

if ($Mode -ne 'auto' -and $repoKind -ne $Mode) {
    Add-Check 'repo.mode' fail "Requested mode $Mode but detected $repoKind"
}

if ($repoKind -eq 'template') {
    Test-TemplateRepo
} elseif ($repoKind -eq 'research') {
    Test-ResearchRepo
}

if ($Deep) {
    Test-Tool 'git'
    Test-Tool 'rg'
    Test-Tool 'powershell'
}

$failCount = @($checks | Where-Object { $_.status -eq 'fail' }).Count
$warnCount = @($checks | Where-Object { $_.status -eq 'warn' }).Count
$okCount = @($checks | Where-Object { $_.status -eq 'ok' }).Count

$status = 'ok'
if ($failCount -gt 0) {
    $status = 'fail'
} elseif ($warnCount -gt 0) {
    $status = 'warn'
}

$result = [ordered]@{
    status = $status
    repo_kind = $repoKind
    repo_path = [string]$root
    deep = [bool]$Deep
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    summary = [ordered]@{
        ok = $okCount
        warn = $warnCount
        fail = $failCount
    }
    checks = @($checks.ToArray())
}

if ($Json) {
    $result | ConvertTo-Json -Depth 24
} else {
    Write-Output "1C Autoresearch Doctor"
    Write-Output ""
    Write-Output "Repo kind: $repoKind"
    Write-Output "Repo path: $root"
    Write-Output "Status: $($status.ToUpperInvariant())"
    Write-Output "Checks: ok=$okCount warn=$warnCount fail=$failCount"
    Write-Output ""
    foreach ($check in $checks) {
        $label = $check.status.ToUpperInvariant()
        Write-Output "[$label] $($check.id): $($check.message)"
    }
}

if ($failCount -gt 0) {
    exit 1
}
if ($Strict -and $warnCount -gt 0) {
    exit 2
}
exit 0
