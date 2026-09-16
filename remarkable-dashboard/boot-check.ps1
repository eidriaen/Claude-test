<#
.SYNOPSIS
    After a reboot: wait, then check that everything the mini PC must run is
    actually running, and write a report a later Claude session can read.

.DESCRIPTION
    Registered by install-task.ps1 as "reMarkable Boot Check" (at startup, no
    password needed). It waits for the network and the other tasks, then
    checks: signed-in desktop session, the reMarkable tasks, the phone page on
    :8080, the headless Claude remote-control session, Tailscale, sshd, RDP and
    OneDrive. It writes out\boot-report-<timestamp>.txt and appends one
    "boot-check:" line to runs.log, so `Get-Content runs.log -Tail 5` answers
    "did the machine come back properly?".

.EXAMPLE
    .\boot-check.ps1 -NoWait     # run the checks right now
#>
param([switch]$NoWait, [int]$WaitSeconds = 180)

$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot
if (-not $NoWait) { Start-Sleep -Seconds $WaitSeconds }

$lines = @()
$problems = @()
function Check([string]$name, [bool]$ok, [string]$detail) {
    $mark = if ($ok) { 'ok' } else { '!!' }
    $script:lines += ("  [{0}] {1,-28} {2}" -f $mark, $name, $detail)
    if (-not $ok) { $script:problems += $name }
}

$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$lines += "Boot check $(Get-Date -Format 's')  (booted $($boot.ToString('s')))"

# Desktop session: is anyone signed in, or is the PC sitting at the PIN screen?
$sessions = (quser 2>$null | Select-Object -Skip 1) -join ' | '
$signedIn = [bool]($sessions -match 'adria')
$sessionDetail = if ($signedIn) { $sessions.Trim() } else { 'nobody signed in (PIN screen). Headless tasks still run; OneDrive and any visible window do not.' }
# Not counted as a problem: everything important is headless now. Reported so
# the autologon question has an answer.
$lines += ("  [{0}] {1,-28} {2}" -f $(if ($signedIn) { 'ok' } else { '--' }), 'signed-in session', $sessionDetail)

foreach ($t in 'reMarkable Daily Sheet', 'reMarkable Sheet Server', 'Claude remote control', 'reMarkable Boot Check') {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if (-not $task) { Check "task: $t" $false 'not registered'; continue }
    $i = $task | Get-ScheduledTaskInfo
    # 267009 = currently running, 267011 = has not run yet, 267014 = terminated by user
    $ok = ($task.State -in 'Ready', 'Running') -and ($i.LastTaskResult -in 0, 267009, 267011)
    Check "task: $t" $ok ("state={0} last={1} result={2} logon={3}" -f $task.State, $i.LastRunTime.ToString('HH:mm'), $i.LastTaskResult, $task.Principal.LogonType)
}

try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/' -UseBasicParsing -TimeoutSec 8
    Check 'phone page :8080' ($r.StatusCode -eq 200) "HTTP $($r.StatusCode)"
} catch { Check 'phone page :8080' $false $_.Exception.Message }

$rc = Get-CimInstance Win32_Process -Filter "Name='claude.exe'" | Where-Object { $_.CommandLine -match 'remote-control' }
$rcDetail = if ($rc) { "pid $(@($rc | ForEach-Object ProcessId) -join ',')" } else { 'no remote-control process' }
Check 'claude remote-control' ([bool]$rc) $rcDetail

$ts = & tailscale status --self --peers=false 2>$null
Check 'tailscale' ([bool]($ts -match '100\.')) (($ts | Select-Object -First 1) -as [string])

Check 'sshd' ((Get-Service sshd -ErrorAction SilentlyContinue).Status -eq 'Running') 'service'
Check 'rdp (3389)' ([bool](Get-NetTCPConnection -LocalPort 3389 -State Listen -ErrorAction SilentlyContinue)) 'listening'
$od = [bool](Get-Process OneDrive -ErrorAction SilentlyContinue)
if ($signedIn) { Check 'onedrive' $od 'process' } else { $lines += ("  [--] {0,-28} {1}" -f 'onedrive', 'not running: needs a signed-in session') }

$last = Get-Content runs.log -Tail 1 -ErrorAction SilentlyContinue
$lines += "  last runs.log line: $last"

$summary = if ($problems.Count -eq 0) { 'boot-check: all OK' } else { "boot-check: PROBLEMS - $($problems -join ', ')" }
if (-not $signedIn) { $summary += ' (nobody signed in)' }
$lines += $summary

New-Item -ItemType Directory -Force out | Out-Null
$report = Join-Path $PSScriptRoot ("out\boot-report-{0}.txt" -f (Get-Date -Format 'yyyy-MM-dd_HHmm'))
$lines | Set-Content -LiteralPath $report -Encoding utf8
Add-Content -LiteralPath (Join-Path $PSScriptRoot 'runs.log') -Value ("{0}  {1}" -f (Get-Date -Format 's'), $summary) -Encoding utf8
$lines | Write-Host
if ($problems.Count -gt 0) { exit 1 }
