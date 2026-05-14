param(
    [Parameter(Mandatory = $true)]
    [string]$Id,

    [Parameter(Mandatory = $true)]
    [ValidateSet('pending', 'claimed', 'evidence_pack', 'drafted', 'needs_review', 'needs_followup', 'blocked', 'done', 'skipped')]
    [string]$Status,

    [string]$QueuePath,
    [string]$ClaimedBy = 'codex',
    [string]$ResultSummary
)

$ErrorActionPreference = 'Stop'

if (-not $QueuePath) {
    $QueuePath = Join-Path $PSScriptRoot '..\..\analysis\queue\tasks.jsonl'
}

function Set-JsonProperty {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Object,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [object]$Value
    )

    if ($Object.PSObject.Properties.Name.Contains($Name)) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

$resolvedQueuePath = Resolve-Path -LiteralPath $QueuePath
$now = (Get-Date).ToUniversalTime().ToString("o")
$found = $false
$updatedTask = $null
$updatedLines = New-Object System.Collections.Generic.List[string]

Get-Content -LiteralPath $resolvedQueuePath | ForEach-Object {
    $line = $_.Trim()
    if ($line.Length -eq 0) {
        return
    }

    $task = $line | ConvertFrom-Json
    if ($task.id -eq $Id) {
        $found = $true
        Set-JsonProperty -Object $task -Name 'status' -Value $Status
        Set-JsonProperty -Object $task -Name 'updated_at' -Value $now

        if ($Status -eq 'claimed') {
            Set-JsonProperty -Object $task -Name 'claimed_by' -Value $ClaimedBy
            Set-JsonProperty -Object $task -Name 'claimed_at' -Value $now
        }

        if ($Status -eq 'done' -or $Status -eq 'skipped') {
            Set-JsonProperty -Object $task -Name 'completed_at' -Value $now
        }

        if ($ResultSummary) {
            Set-JsonProperty -Object $task -Name 'result_summary' -Value $ResultSummary
        }

        $updatedTask = $task
    }

    $updatedLines.Add(($task | ConvertTo-Json -Depth 20 -Compress))
}

if (-not $found) {
    throw "Task not found: $Id"
}

Set-Content -LiteralPath $resolvedQueuePath -Value $updatedLines -Encoding UTF8
$updatedTask | ConvertTo-Json -Depth 20
