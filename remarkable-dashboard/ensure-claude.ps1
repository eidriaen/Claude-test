<#
.SYNOPSIS
    Keep Claude Code reachable on this machine, with or without anyone signed in.

.DESCRIPTION
    Called by the scheduled task "Claude remote control" every few minutes. It
    runs a headless `claude remote-control` session that shows up in
    claude.ai/code and the Claude mobile app under the name "Mini-server". The
    task runs as a no-password (S4U) task, so it comes back after any reboot,
    including one that leaves the PC at the PIN screen.

    The first start creates the session and records its id in
    out\remote-session.txt; every later start (after a reboot, or after the
    process died) reattaches to that same session, so the "Mini-server" chat
    keeps its history. If it cannot be reattached it falls back to a fresh
    session with the same name, so there is always something to connect to.
    It starts in C:\Users\adria, the project folder whose memory notes describe
    the reMarkable setup, so the remote session knows the project.

    -Window opens a visible Claude Code window instead, resuming the same chat
    (only makes sense while somebody is signed in; run it by hand when you are
    at the machine).

.EXAMPLE
    .\ensure-claude.ps1                    # start the headless session if none is running
    .\ensure-claude.ps1 -Install           # register the task (every 5 minutes, S4U)
    .\ensure-claude.ps1 -Window            # a visible window resuming the same chat
    .\ensure-claude.ps1 -Remove
#>
param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Window,
    [int]$Every = 5,
    [string]$Name = 'Mini-server',
    # Server-side id (session_01...) of the remote session to reattach to.
    # Empty = use the one recorded in out\remote-session.txt from the last
    # fresh start, so the same "Mini-server" chat survives reboots. (The
    # original development chat cannot be reattached: it was never bridged.
    # Its local transcript is fe5e79cb-67e0-4961-ae0c-4959d634b616.)
    [string]$SessionId = '',
    # Local transcript of the original reMarkable development chat, for -Window.
    [string]$DevSessionId = 'fe5e79cb-67e0-4961-ae0c-4959d634b616',
    # Where that session lives; remote-control must start in the same folder.
    [string]$ProjectDir = 'C:\Users\adria'
)

$ErrorActionPreference = 'Stop'
$taskName   = 'Claude remote control'
$legacyName = 'Claude Code window'
$here       = $PSScriptRoot
$log        = Join-Path $here 'runs.log'

function Write-Log([string]$msg) {
    Add-Content -LiteralPath $log -Value ("{0}  {1}" -f (Get-Date -Format 's'), $msg) -Encoding utf8
}

if ($Remove) {
    foreach ($n in @($taskName, $legacyName)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            Write-Host "Removed scheduled task '$n'."
        }
    }
    return
}

if ($Install) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`"" `
        -WorkingDirectory $here
    # -Once with a repetition interval and no duration repeats forever.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes $Every)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
    # S4U = "run whether user is logged on or not" without storing a password.
    # No desktop is needed for a headless remote-control session.
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description "Keeps a headless 'claude remote-control' session ($Name) running so claude.ai/code and the mobile app can always reach this PC." -Force | Out-Null
    if (Get-ScheduledTask -TaskName $legacyName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $legacyName -Confirm:$false
        Write-Host "Removed the old '$legacyName' task."
    }
    Write-Host "Scheduled '$taskName' every $Every minutes (S4U, headless). Session name: $Name."
    return
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) { $claude = $claude.Source }
else {
    $fallback = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
    if (Test-Path -LiteralPath $fallback) { $claude = $fallback } else { Write-Log 'claude: not found on PATH'; exit 1 }
}

if ($Window) {
    # A visible window, for when you are at the machine, resuming the original
    # development chat (a local session, so --resume rather than remote-control).
    Start-Process powershell.exe -WorkingDirectory $ProjectDir `
        -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "& '$claude' --resume $DevSessionId")
    return
}

# One instance of this script at a time: a second tick during the 25-second
# start-up wait below would otherwise launch a second session.
$mutex = New-Object System.Threading.Mutex($false, 'Global\ClaudeRemoteControlEnsure')
if (-not $mutex.WaitOne(0)) { exit 0 }

# Already running? (Match the remote-control process itself, not a window.)
$running = Get-CimInstance Win32_Process -Filter "Name='claude.exe'" | Where-Object { $_.CommandLine -match 'remote-control' }
if ($running) { exit 0 }

$out   = Join-Path $here 'out\remote-control.log'
$saved = Join-Path $here 'out\remote-session.txt'
New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
if (-not $SessionId -and (Test-Path -LiteralPath $saved)) {
    $SessionId = (Get-Content -LiteralPath $saved -TotalCount 1 -ErrorAction SilentlyContinue).Trim()
}

function Start-Remote([string[]]$extra) {
    $args = @('remote-control', '--name', $Name) + $extra
    $p = Start-Process -FilePath $claude -ArgumentList $args -WorkingDirectory $ProjectDir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError "$out.err" -PassThru
    # A bad session id makes claude exit within seconds; a good one keeps running.
    Start-Sleep -Seconds 25
    if ($p.HasExited) { return $null }
    return $p
}

function Save-SessionId {
    # The bridge prints the session link once the session exists. Remember it
    # so the next start (after a reboot) reattaches instead of creating a new one.
    for ($i = 0; $i -lt 6; $i++) {
        $m = [regex]::Match(((Get-Content $out -Raw -ErrorAction SilentlyContinue) -as [string]), 'session_01[A-Za-z0-9]{20,}')
        if ($m.Success) { Set-Content -LiteralPath $saved -Value $m.Value -Encoding ascii; return $m.Value }
        Start-Sleep -Seconds 5
    }
    return $null
}

if ($SessionId) {
    $p = Start-Remote @('--session-id', $SessionId)
    if ($p) {
        Write-Log "claude remote-control '$Name' up (pid $($p.Id)), resumed $SessionId"
        exit 0
    }
    $err = (Get-Content "$out.err" -Tail 2 -ErrorAction SilentlyContinue) -join ' '
    Write-Log "claude remote-control: could not resume $SessionId ($err) - starting a fresh session"
}
$p = Start-Remote @()
if ($p) {
    $id = Save-SessionId
    Write-Log "claude remote-control '$Name' up (pid $($p.Id)), fresh session $(if ($id) { $id } else { '(id not seen yet)' })"
    exit 0
}
Write-Log "claude remote-control FAILED: $((Get-Content "$out.err" -Tail 2 -ErrorAction SilentlyContinue) -join ' ')"
exit 1
