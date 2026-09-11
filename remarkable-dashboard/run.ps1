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

$args = $pre + @('-m', 'daily_sheet', 'generate')
if ($Fixtures) { $args += '--fixtures' }
if ($DryRun)   { $args += '--dry-run' }
if ($Date)     { $args += @('--date', $Date) }

Write-Log "start: $exe $($args -join ' ')"
& $exe @args
$code = $LASTEXITCODE

if ($code -ne 0) { Write-Log "FAILED with exit code $code" }
exit $code
