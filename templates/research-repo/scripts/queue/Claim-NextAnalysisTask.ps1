param(
    [string]$QueuePath,
    [string]$Status = 'pending',
    [string]$Type,
    [string]$ClaimedBy = 'codex',
    [int]$LockTimeoutSeconds = 10
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

function Invoke-WithQueueLock {
    param(
        [string]$Path,
        [scriptblock]$Body
    )

    $lockPath = "$Path.lock"
    $deadline = (Get-Date).AddSeconds($LockTimeoutSeconds)
    $stream = $null

    while ($null -eq $stream) {
        try {
            $stream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        } catch [System.IO.IOException] {
            if ((Get-Date) -ge $deadline) {
                throw "Timed out waiting for queue lock: $lockPath"
            }
            Start-Sleep -Milliseconds 100
        }
    }

    try {
        & $Body
    } finally {
        $stream.Dispose()
    }
}

function Write-QueueLines {
    param(
        [string]$Path,
        [System.Collections.Generic.List[string]]$Lines
    )

    $tempPath = "$Path.tmp.$PID.$([Guid]::NewGuid().ToString('N'))"
    try {
        Set-Content -LiteralPath $tempPath -Value $Lines -Encoding UTF8
        Move-Item -LiteralPath $tempPath -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force
        }
    }
}

$resolvedQueuePath = Resolve-Path -LiteralPath $QueuePath
$script:claimedTask = $null

Invoke-WithQueueLock -Path $resolvedQueuePath -Body {
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
        foreach ($dependency in (Get-ArrayValue $_.dependencies)) {
            if (-not [string]::IsNullOrWhiteSpace([string]$dependency) -and -not $doneIds.ContainsKey([string]$dependency)) {
                return $false
            }
        }
        return $true
    }

    $next = $candidates | Sort-Object @{ Expression = 'priority'; Descending = $true }, @{ Expression = 'id'; Descending = $false } | Select-Object -First 1
    if (-not $next) {
        return
    }

    $now = (Get-Date).ToUniversalTime().ToString("o")
    Set-JsonProperty -Object $next -Name 'status' -Value 'claimed'
    Set-JsonProperty -Object $next -Name 'updated_at' -Value $now
    Set-JsonProperty -Object $next -Name 'claimed_by' -Value $ClaimedBy
    Set-JsonProperty -Object $next -Name 'claimed_at' -Value $now
    $script:claimedTask = $next

    $updatedLines = New-Object System.Collections.Generic.List[string]
    foreach ($task in $tasks) {
        $updatedLines.Add(($task | ConvertTo-Json -Depth 20 -Compress))
    }

    Write-QueueLines -Path $resolvedQueuePath -Lines $updatedLines
}

if ($null -eq $script:claimedTask) {
    Write-Output '{}'
    exit 0
}

$script:claimedTask | ConvertTo-Json -Depth 20
