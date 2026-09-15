<#
.SYNOPSIS
    One run of the daily loop. This is what Task Scheduler calls.

.DESCRIPTION
    Wraps `py -m daily_sheet generate` so the scheduled task has a single stable
    entry point: it fixes the working directory (Task Scheduler starts you in
    system32), appends to a log, and returns a real exit code so a failed push
    shows up as a failed task in the scheduler UI rather than silently.

.EXAMPLE
    .\run.ps1                 # the real thing
    .\run.ps1 -DryRun         # render but do not push
    .\run.ps1 -Fixtures -DryRun
#>
param(
    [switch]$DryRun,
    [switch]$Fixtures,
    [switch]$Sync,
    [switch]$Auto,
    [string]$GenerateAt = '08:00',
    [string]$Date
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$log = Join-Path $PSScriptRoot 'runs.log'

function Write-Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 's'), $msg
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding utf8
}

# Prefer the py launcher; fall back to python on PATH.
$exe, $pre = if (Get-Command py -ErrorAction SilentlyContinue) { 'py', @('-3') }
             elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python', @() }
             else { $null, $null }

if (-not $exe) {
    Write-Log 'ERROR: no Python found on PATH (tried py, python).'
    exit 1
}

# -Auto folds the two old tasks (08:00 generate, 15-minute sync) into one
# timer. A marker file records that today's sheet has been pushed; until it
# exists, every tick from -GenerateAt onwards tries generate (so a failed push
# is retried five minutes later instead of waiting for tomorrow), and once it
# exists every tick is a sync. Before -GenerateAt there is nothing to do.
$marker = $null
if ($Auto) {
    $today = Get-Date -Format 'yyyy-MM-dd'
    $marker = Join-Path $PSScriptRoot "out\pushed-$today"
    if (Test-Path -LiteralPath $marker) {
        $Sync = $true
    } else {
        try { $from = [datetime]::ParseExact($GenerateAt, 'HH:mm', $null) }
        catch { Write-Log "ERROR: -GenerateAt must look like 08:00. Got '$GenerateAt'."; exit 1 }
        if ((Get-Date).TimeOfDay -lt $from.TimeOfDay) {
            # Quiet: this fires every five minutes all night.
            exit 0
        }
        $Sync = $false
    }
}

$args = $pre + @('-m', 'daily_sheet', $(if ($Sync) { 'sync' } else { 'generate' }))
if (-not $Sync) {
    if ($Fixtures) { $args += '--fixtures' }
    if ($DryRun)   { $args += '--dry-run' }
}
if ($Date) { $args += @('--date', $Date) }

Write-Log "start: $exe $($args -join ' ')"
& $exe @args
$code = $LASTEXITCODE

if ($code -ne 0) { Write-Log "FAILED with exit code $code" }
elseif ($marker -and -not $Sync -and -not $DryRun) {
    New-Item -ItemType Directory -Force (Split-Path $marker) | Out-Null
    Set-Content -LiteralPath $marker -Value (Get-Date -Format 's') -Encoding ascii
}
exit $code
