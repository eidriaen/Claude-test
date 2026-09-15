<#
.SYNOPSIS
    Keep a Claude Code window open on this machine.

.DESCRIPTION
    Called by the scheduled task "Claude Code window" every few minutes. If a
    claude process is already running it does nothing; otherwise it opens a
    PowerShell window in this folder and starts Claude Code in it. Closing the
    window therefore only buys you until the next tick, which is the point.

.EXAMPLE
    .\ensure-claude.ps1            # start one if none is running
    .\ensure-claude.ps1 -Install   # register the task (every 5 minutes)
    .\ensure-claude.ps1 -Install -Every 10
    .\ensure-claude.ps1 -Remove
#>
param(
    [switch]$Install,
    [switch]$Remove,
    [int]$Every = 5
)

$ErrorActionPreference = 'Stop'
$taskName = 'Claude Code window'
$here = $PSScriptRoot

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed scheduled task '$taskName'."
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
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    # Interactive logon type: the task has to open a window on the desktop, so
    # it runs only while this user is logged on (like the sheet task).
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Opens a Claude Code window in $here if none is running." -Force | Out-Null
    Write-Host "Scheduled '$taskName' every $Every minutes."
    return
}

if (Get-Process -Name claude -ErrorAction SilentlyContinue) {
    exit 0
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    $fallback = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
    if (Test-Path -LiteralPath $fallback) { $claude = $fallback } else { Write-Error 'claude not found on PATH'; exit 1 }
} else {
    $claude = $claude.Source
}

# A visible window, not hidden: this is the one you walk up to (or RDP into).
Start-Process powershell.exe -WorkingDirectory $here `
    -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "& '$claude'")
