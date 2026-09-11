<#
.SYNOPSIS
    Register (or update) the Windows scheduled task that generates and pushes
    the daily sheet.

.DESCRIPTION
    Creates a task named "reMarkable Daily Sheet" that runs run.ps1 every day at
    the given time. Runs only while you are logged on -- that avoids storing your
    password, which is the usual reason these tasks get abandoned.

    If the machine is asleep or off at the scheduled time the run is not lost:
    StartWhenAvailable makes it fire as soon as the machine is back, so a sheet
    still lands before you pick the tablet up.

    Re-running this script overwrites the existing task, so it doubles as the way
    to change the time.

.EXAMPLE
    .\install-task.ps1                    # 08:00 daily
    .\install-task.ps1 -At 07:30
    .\install-task.ps1 -Remove
#>
param(
    [string]$At = '08:00',
    [int]$SyncEvery = 0,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'reMarkable Daily Sheet'
$syncName = 'reMarkable Sync'
$runner   = Join-Path $PSScriptRoot 'run.ps1'

if ($Remove) {
    foreach ($n in @($taskName, $syncName)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            Write-Host "Removed scheduled task '$n'."
        }
    }
    return
}

if (-not (Test-Path -LiteralPath $runner)) {
    throw "run.ps1 not found next to this script ($runner)."
}
try { $when = [datetime]::ParseExact($At, 'HH:mm', $null) }
catch { throw "-At must look like 08:00 (24-hour). Got '$At'." }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" `
    -WorkingDirectory $PSScriptRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $when

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName    $taskName `
    -Description 'Generates the daily sheet and pushes it to the reMarkable.' `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Force | Out-Null

Write-Host "Scheduled '$taskName' daily at $At."

if ($SyncEvery -gt 0) {
    # Reads the ticks off today's sheet and pushes them to Asana. It never
    # re-renders or archives, so it is safe to run while you are writing on the
    # sheet -- marks added later are picked up by the next pass.
    $syncAction = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Sync" `
        -WorkingDirectory $PSScriptRoot

    $syncTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(7) `
        -RepetitionInterval (New-TimeSpan -Minutes $SyncEvery) `
        -RepetitionDuration (New-TimeSpan -Hours 23)

    Register-ScheduledTask `
        -TaskName    $syncName `
        -Description 'Reads ticks off today''s sheet and completes them in Asana.' `
        -Action      $syncAction `
        -Trigger     $syncTrigger `
        -Settings    $settings `
        -Force | Out-Null

    Write-Host "Scheduled '$syncName' every $SyncEvery minutes."
}

Write-Host ''
Write-Host 'Check it:'
Write-Host "  Get-ScheduledTask -TaskName '$taskName'"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'    # run it now"
Write-Host "  Get-Content runs.log -Tail 20"
Write-Host ''
Write-Host 'Note: runs only while you are logged on. If the machine is off at'
Write-Host "$At the run fires as soon as it is back."
