param(
    [string]$QueuePath,
    [string]$Status = 'pending',
    [string]$Type,
    [switch]$All,
    [switch]$IncludeBlockedByDependencies
)

$ErrorActionPreference = 'Stop'

if (-not $QueuePath) {
    $QueuePath = Join-Path $PSScriptRoot '..\..\analysis\queue\tasks.jsonl'
}

$resolvedQueuePath = Resolve-Path -LiteralPath $QueuePath
$tasks = @()

Get-Content -LiteralPath $resolvedQueuePath | ForEach-Object {
    $line = $_.Trim()
    if ($line.Length -eq 0) {
        return
    }
    $tasks += $line | ConvertFrom-Json
}

$doneIds = @{}
foreach ($task in $tasks) {
    if ($task.status -eq 'done' -or $task.status -eq 'skipped') {
        $doneIds[$task.id] = $true
    }
}

$candidates = $tasks | Where-Object {
    if ($_.status -ne $Status) {
        return $false
    }
    if ($Type -and $_.type -ne $Type) {
        return $false
    }
    if ($IncludeBlockedByDependencies) {
        return $true
    }
    if (-not $_.dependencies) {
        return $true
    }
    foreach ($dependency in $_.dependencies) {
        if (-not $doneIds.ContainsKey([string]$dependency)) {
            return $false
        }
    }
    return $true
}

$ordered = $candidates | Sort-Object @{ Expression = 'priority'; Descending = $true }, @{ Expression = 'id'; Descending = $false }

if ($All) {
    $ordered | ConvertTo-Json -Depth 12
    exit 0
}

$next = $ordered | Select-Object -First 1
if (-not $next) {
    Write-Output '{}'
    exit 0
}

$next | ConvertTo-Json -Depth 12
