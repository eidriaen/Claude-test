<#
.SYNOPSIS
    Register (or update) the Windows scheduled task that keeps the daily sheet
    running: one task, every few minutes.

.DESCRIPTION
    Creates a task named "reMarkable Daily Sheet" that calls run.ps1 -Auto every
    -Every minutes, all day. run.ps1 decides what each tick does: from -At
    onwards it generates and pushes today's sheet (retrying on the next tick if
    the push failed), and once the sheet is up every tick reads the ticks off it
    into Asana. Before -At a tick does nothing.

    Runs only while you are logged on -- that avoids storing your password,
    which is the usual reason these tasks get abandoned.

    Re-running this script overwrites the existing task, so it doubles as the
    way to change the time or the interval. It also removes the old separate
    "reMarkable Sync" task from earlier versions.

.EXAMPLE
    .\install-task.ps1                    # every 5 min, sheet from 08:00
    .\install-task.ps1 -At 07:30 -Every 10
    .\install-task.ps1 -Server            # also start the phone server at logon
    .\install-task.ps1 -Remove
#>
param(
    [string]$At = '08:00',
    [int]$Every = 5,
    [int]$SyncEvery = 0,          # old name for -Every, kept so setup.ps1 still works
    [int]$Port = 8080,
    [switch]$Server,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName   = 'reMarkable Daily Sheet'
$legacySync = 'reMarkable Sync'
$serverName = 'reMarkable Sheet Server'
$runner     = Join-Path $PSScriptRoot 'run.ps1'

if ($SyncEvery -gt 0) { $Every = $SyncEvery }

if ($Remove) {
    foreach ($n in @($taskName, $legacySync, $serverName)) {
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
try { [void][datetime]::ParseExact($At, 'HH:mm', $null) }
catch { throw "-At must look like 08:00 (24-hour). Got '$At'." }
if ($Every -lt 1 -or $Every -gt 720) { throw "-Every must be between 1 and 720 minutes. Got $Every." }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Auto -GenerateAt $At" `
    -WorkingDirectory $PSScriptRoot

# A -Once trigger with a repetition interval and no duration repeats forever.
# (A daily trigger cannot carry repetition parameters from this cmdlet, and a
# 23-hour duration on -Once fires on one day only -- which is how the old
# sync task silently died.)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $Every)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName    $taskName `
    -Description "Every $Every min: pushes the daily sheet from $At, then reads its ticks into Asana." `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Force | Out-Null

Write-Host "Scheduled '$taskName' every $Every minutes (sheet generated from $At)."

if (Get-ScheduledTask -TaskName $legacySync -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $legacySync -Confirm:$false
    Write-Host "Removed the old separate '$legacySync' task -- '$taskName' does both now."
}

if ($Server) {
    # The phone server, started at logon and restarted if it ever dies. It is
    # long-running rather than scheduled, so no repetition trigger -- one
    # instance, kept alive.
    # Windows PowerShell 5.1 is what ships on these machines, so no ?. here.
    $pyw = $null
    foreach ($exe in @('pythonw.exe', 'pyw.exe')) {
        $cmd = Get-Command $exe -ErrorAction SilentlyContinue
        if ($cmd) { $pyw = $cmd.Source; break }
    }
    if (-not $pyw) {
        Write-Warning "No pythonw.exe on PATH -- skipping '$serverName'. Start it by hand with 'Daily Sheet Server.bat'."
    } else {
        $envFile = Join-Path $PSScriptRoot '.env'
        if (-not ((Test-Path -LiteralPath $envFile) -and
                  (Select-String -LiteralPath $envFile -Pattern '^\s*WEB_TOKEN\s*=\s*\S' -Quiet))) {
            # Without a token in .env the server invents one per restart and
            # prints it to a console nobody is looking at -- so the phone would
            # be locked out after every reboot.
            Write-Warning 'No WEB_TOKEN in .env. Set one before relying on the server task, or the token changes on every restart.'
        }

        $serverAction = New-ScheduledTaskAction `
            -Execute $pyw `
            -Argument "`"$(Join-Path $PSScriptRoot 'serve.py')`" --port $Port --quiet" `
            -WorkingDirectory $PSScriptRoot

        # Scoped to this user: an any-user logon trigger needs an elevated
        # shell to register, and the task must run as the user rmapi is
        # paired with anyway.
        $serverTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

        $serverSettings = New-ScheduledTaskSettingsSet `
            -DontStopIfGoingOnBatteries `
            -AllowStartIfOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1)

        Register-ScheduledTask `
            -TaskName    $serverName `
            -Description 'Serves the phone page that presses the same buttons as the window.' `
            -Action      $serverAction `
            -Trigger     $serverTrigger `
            -Settings    $serverSettings `
            -Force | Out-Null

        Write-Host "Scheduled '$serverName' at logon on port $Port."
    }
}

Write-Host ''
Write-Host 'Check it:'
Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'    # run a tick now"
Write-Host "  Get-Content runs.log -Tail 20"
Write-Host ''
Write-Host 'Note: runs only while you are logged on. If the machine is off at'
Write-Host "$At the sheet goes up on the first tick after it is back."
