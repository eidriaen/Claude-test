<#
.SYNOPSIS
    One-shot setup for the machine that owns the daily sheet loop.

.DESCRIPTION
    Install Windows, run this once, walk away. It installs Python, Git, rmapi
    and Tailscale, clones the project, registers the scheduled tasks, turns on
    SSH and Remote Desktop, and stops the machine sleeping.

    Three things it cannot do for you, because each needs a human once:
    signing into OneDrive, pairing rmapi with a code from my.remarkable.com,
    and pasting your Asana token into .env. It prints them as a checklist at
    the end, and SSH is up by then so you can do two of the three remotely.

    Nothing here is destructive. An existing .env is never overwritten, and a
    step that fails is reported at the end rather than stopping the rest --
    a missing Tailscale should not cost you the scheduled tasks.

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -Root D:\DailySheet -Port 9000
    .\setup.ps1 -NoRdp -NoTailscale
    .\setup.ps1 -NoClaudeCode          # skip Node and Claude Code
#>
param(
    [string]$Root = 'C:\Claude-test',
    [string]$Branch = 'ReMarkable-dashboard',
    [string]$Repo = 'https://github.com/eidriaen/Claude-test.git',
    [string]$At = '08:00',
    [int]$SyncEvery = 15,
    [int]$Port = 8080,
    [string]$EnvFrom = '',
    [string]$BatDir = '',
    [switch]$NoTailscale,
    [switch]$NoSsh,
    [switch]$NoRdp,
    [switch]$NoTasks,
    [switch]$NoClaudeCode
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Re-launch elevated. Half of this (Windows capabilities, services, firewall,
# scheduled tasks) simply cannot be done otherwise, and finding that out
# half way through is worse than asking up front.
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Needs administrator -- asking Windows to re-launch...'
    $line = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        $line += if ($kv.Value -is [switch]) { " -$($kv.Key)" } else { " -$($kv.Key) `"$($kv.Value)`"" }
    }
    Start-Process powershell -ArgumentList $line -Verb RunAs
    exit
}

$project = Join-Path $Root 'remarkable-dashboard'
$problems = New-Object System.Collections.ArrayList
$todo     = New-Object System.Collections.ArrayList

function Step([string]$name) {
    Write-Host ''
    Write-Host "== $name" -ForegroundColor Cyan
}

function Note([string]$text) { Write-Host "   $text" -ForegroundColor DarkGray }
function Good([string]$text) { Write-Host "   $text" -ForegroundColor Green }

function Problem([string]$text) {
    Write-Host "   $text" -ForegroundColor Yellow
    [void]$problems.Add($text)
}

function Todo([string]$text) { [void]$todo.Add($text) }

function Update-Path {
    # winget edits the registry, not this process. Without re-reading it, the
    # python we just installed is not on PATH until a new window opens.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Have([string]$exe) {
    return [bool](Get-Command $exe -ErrorAction SilentlyContinue)
}

function Get-EnvValue([string[]]$lines, [string]$key) {
    # Read one value out of .env lines. Empty string when unset or blank.
    foreach ($line in $lines) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $name, $value = $t -split '=', 2
        if ($name.Trim() -eq $key) { return $value.Trim() }
    }
    return ''
}

function Set-EnvValue([string[]]$lines, [string]$key, [string]$value, [switch]$Force) {
    # Set KEY=value, keeping the file's order and comments. Without -Force an
    # existing non-empty value is left alone: an .env carried over from a
    # working machine is the thing we most want to preserve, and "helpfully"
    # rewriting a token would be the worst kind of tidy-up.
    $out = @()
    $seen = $false
    foreach ($line in $lines) {
        $t = $line.Trim()
        if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) {
            $name = ($t -split '=', 2)[0].Trim()
            if ($name -eq $key) {
                $seen = $true
                $existing = ($t -split '=', 2)[1].Trim()
                $out += if ($Force -or -not $existing) { "$key=$value" } else { $line }
                continue
            }
        }
        $out += $line
    }
    if (-not $seen) { $out += "$key=$value" }
    return $out
}

function Install-App([string]$id, [string]$exe, [string]$label) {
    if (Have $exe) { Good "$label already installed"; return $true }
    Note "installing $label..."
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
    Update-Path
    if (Have $exe) { Good "$label installed"; return $true }
    Problem "$label did not install -- install it by hand and re-run this script"
    return $false
}

Write-Host ''
Write-Host 'reMarkable Daily Sheet -- machine setup' -ForegroundColor White
Write-Host "   project root : $Root"
Write-Host "   branch       : $Branch"

# ---------------------------------------------------------------- winget
Step 'Package manager'
if (-not (Have 'winget')) {
    Write-Host '   winget is missing. It ships with Windows 10 1809+ as "App Installer".' -ForegroundColor Red
    Write-Host '   Install it from the Microsoft Store, then run this script again.' -ForegroundColor Red
    exit 1
}
Good 'winget present'

# ---------------------------------------------------------------- tools
Step 'Python, Git, Tailscale'
$havePython = Install-App 'Python.Python.3.12' 'py' 'Python 3.12'
$haveGit    = Install-App 'Git.Git' 'git' 'Git'
if (-not $NoTailscale) {
    if (Install-App 'tailscale.tailscale' 'tailscale' 'Tailscale') {
        Todo 'Run "tailscale up" and sign in -- that is how your phone reaches this machine from outside.'
    }
} else {
    Note 'skipping Tailscale (-NoTailscale)'
}

# ---------------------------------------------------------------- code
Step 'Project code'
if (-not $haveGit) {
    Problem 'no git, so the project cannot be cloned'
} elseif (Test-Path -LiteralPath (Join-Path $Root '.git')) {
    Note 'already cloned -- pulling'
    git -C $Root fetch origin $Branch
    git -C $Root checkout $Branch
    git -C $Root pull --ff-only origin $Branch
    Good 'up to date'
} else {
    Note "cloning into $Root"
    git clone -b $Branch $Repo $Root
    Good 'cloned'
}

# ---------------------------------------------------------------- python deps
Step 'Python packages'
if ($havePython -and (Test-Path -LiteralPath (Join-Path $project 'requirements.txt'))) {
    Push-Location $project
    try {
        py -m pip install --upgrade pip --quiet
        py -m pip install -r requirements.txt --quiet
        Good 'reportlab, Pillow, numpy, pypdfium2 and friends installed'
    } catch {
        Problem "pip install failed: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
} else {
    Problem 'skipped -- no Python or no requirements.txt'
}

# ---------------------------------------------------------------- rmapi
Step 'rmapi (talks to the reMarkable cloud)'
$rmapi = 'C:\tools\rmapi.exe'
if (Test-Path -LiteralPath $rmapi) {
    Good "already at $rmapi"
} else {
    try {
        New-Item -ItemType Directory -Force -Path 'C:\tools' | Out-Null
        $rel = Invoke-RestMethod 'https://api.github.com/repos/ddvk/rmapi/releases/latest' `
                                 -Headers @{ 'User-Agent' = 'daily-sheet-setup' }
        # Exclude arm rather than matching the Intel name. The Windows builds
        # are "rmapi-win64.zip" and "rmapi-win-arm64.zip" -- no "windows", no
        # "x86_64", so a filter naming the architecture it wants matches
        # nothing, while one ruling out the wrong architecture keeps working
        # when the names change again. Picking the arm build gives "This app
        # can't run on your PC" with nothing to say why.
        $asset = $rel.assets |
            Where-Object { $_.name -match '(?i)win' -and
                           $_.name -notmatch '(?i)arm' -and
                           $_.name -match '(?i)\.zip$' } |
            Select-Object -First 1
        if (-not $asset) {
            throw "no Intel Windows zip in release $($rel.tag_name); assets were: $($rel.assets.name -join ', ')"
        }

        $zip = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
        $dir = Join-Path $env:TEMP 'rmapi-unzip'
        Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive $zip -DestinationPath $dir -Force
        $exe = Get-ChildItem $dir -Filter 'rmapi.exe' -Recurse | Select-Object -First 1
        if (-not $exe) { throw 'no rmapi.exe inside the zip' }
        Copy-Item $exe.FullName $rmapi -Force
        # Run it. A binary for the wrong architecture installs perfectly and
        # then refuses to start, which is not something to discover weeks
        # later when a push fails.
        $ver = (& $rmapi version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw "$($asset.name) installed but will not run: $ver" }
        Good "installed $($asset.name) to $rmapi"
    } catch {
        Problem "could not fetch rmapi: $($_.Exception.Message)"
        Todo 'Download the Windows x86_64 rmapi from https://github.com/ddvk/rmapi/releases and put rmapi.exe in C:\tools.'
    }
}
# Pairing needs a code from a browser, so it cannot be automated -- but it can
# be offered now rather than left on a list to do later.
# Presence of the config is the test, deliberately: running "rmapi ls" unpaired
# prompts for a code, which would hang a script nobody is watching.
$rmapiConf = Join-Path $env:APPDATA 'rmapi\rmapi.conf'
if (-not (Test-Path -LiteralPath $rmapi)) {
    Todo 'Install rmapi, then run it once to pair.'
} elseif (Test-Path -LiteralPath $rmapiConf) {
    Good "already paired (token in $rmapiConf)"
} else {
    Write-Host ''
    Note 'Not paired with the reMarkable cloud yet.'
    Note 'Open https://my.remarkable.com/device/desktop/connect and CHECK IT SHOWS'
    Note 'THE ACCOUNT YOU WANT before copying the code -- pairing the wrong one is'
    Note 'a nuisance to undo.'
    $answer = Read-Host '   Pair now? [Y/n]'
    if ($answer -notmatch '^[nN]') {
        & $rmapi
        if (Test-Path -LiteralPath $rmapiConf) {
            Good 'paired'
        } else {
            Todo 'Run C:\tools\rmapi.exe and paste the one-time code from my.remarkable.com.'
        }
    } else {
        Todo 'Run C:\tools\rmapi.exe and paste the one-time code from my.remarkable.com.'
    }
}
# The token is per-user. If this script was elevated as a different admin
# account, it landed in that account's profile and the scheduled tasks -- which
# run as the logged-on user -- will authenticate as nobody.
Note "paired as $env:USERNAME; the scheduled tasks must run as this user too"

# ---------------------------------------------------------------- .env
Step 'Settings file (.env)'
$envFile = Join-Path $project '.env'

# Bring one you already have. Copying the .env from a working machine is the
# whole of "step 3", so the script looks for it next to itself before asking
# you to type secrets into a headless box over SSH.
if (-not (Test-Path -LiteralPath $envFile)) {
    $candidates = @()
    if ($EnvFrom) { $candidates += $EnvFrom }
    if ($BatDir)  { $candidates += (Join-Path $BatDir '.env') }
    $candidates += (Join-Path $PSScriptRoot '.env')
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c) -and
            ((Resolve-Path $c).Path -ne (Join-Path $project '.env'))) {
            Copy-Item -LiteralPath $c -Destination $envFile
            Good "copied your settings from $c"
            break
        }
    }
}
if (-not (Test-Path -LiteralPath $envFile)) {
    if (Test-Path -LiteralPath (Join-Path $project '.env.example')) {
        Copy-Item (Join-Path $project '.env.example') $envFile
        Note 'started a new .env from the template'
    } else {
        Problem 'no .env and no .env.example -- did the clone work?'
    }
}

if (Test-Path -LiteralPath $envFile) {
    # Top up what a machine can work out for itself, and never touch a value
    # that is already there -- an .env carried over from another machine is the
    # good case, not something to overwrite.
    $lines = @(Get-Content -LiteralPath $envFile)
    $lines = Set-EnvValue $lines 'RMAPI_BIN' $rmapi -Force
    if (-not (Get-EnvValue $lines 'WEB_TOKEN')) {
        $token = -join ((48..57) + (65..90) + (97..122) |
                        Get-Random -Count 24 | ForEach-Object { [char]$_ })
        $lines = Set-EnvValue $lines 'WEB_TOKEN' $token
        Good "made a fixed WEB_TOKEN for the phone page ($token)"
    }
    # An .env carried from another machine names that machine's calendar path,
    # complete with its username -- so a value being present is not the same as
    # it being right here. Look it up whenever the file it points at is missing.
    $cur = Get-EnvValue $lines 'CALENDAR_JSON'
    if (-not $cur -or -not (Test-Path -LiteralPath $cur)) {
        $cal = Get-ChildItem "$env:USERPROFILE\OneDrive*" -Filter 'calendar.json' `
                             -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cal) {
            $lines = Set-EnvValue $lines 'CALENDAR_JSON' $cal.FullName -Force
            Good "found the calendar at $($cal.FullName)"
        } elseif ($cur) {
            Note "CALENDAR_JSON points at $cur, which is not on this machine"
            $lines = Set-EnvValue $lines 'CALENDAR_JSON' '' -Force
        }
    }
    Set-Content -LiteralPath $envFile -Value $lines -Encoding ASCII

    $missing = @('ASANA_PAT', 'ANTHROPIC_API_KEY', 'CALENDAR_JSON') |
               Where-Object { -not (Get-EnvValue $lines $_) }
    if ($missing) {
        Todo "Fill in $($missing -join ', ') in $envFile"
    } else {
        Good 'every setting is present'
    }
}

# ---------------------------------------------------------------- ssh
if (-not $NoSsh) {
    Step 'SSH (so you never need a keyboard on this machine)'
    try {
        $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' |
               Where-Object State -ne 'Installed' | Select-Object -First 1
        if ($cap) {
            Note 'installing the OpenSSH server feature...'
            Add-WindowsCapability -Online -Name $cap.Name | Out-Null
        }
        Set-Service -Name sshd -StartupType Automatic
        Start-Service sshd
        if (-not (Get-NetFirewallRule -Name 'sshd-daily-sheet' -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -Name 'sshd-daily-sheet' -DisplayName 'OpenSSH Server (sshd)' `
                -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Private | Out-Null
        }
        # PowerShell rather than cmd.exe, so an SSH session behaves like the
        # windows you have been typing into all along.
        New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
        New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell `
            -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -PropertyType String -Force | Out-Null
        Good "sshd running -- ssh $env:USERNAME@<this machine>"
    } catch {
        Problem "SSH setup failed: $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- rdp
if (-not $NoRdp) {
    Step 'Remote Desktop (for the things that need a screen)'
    try {
        Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' `
            -Name fDenyTSConnections -Value 0
        Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
        Good 'enabled -- OneDrive sign-in needs this, or a monitor, once'
    } catch {
        Problem "could not enable Remote Desktop: $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- power
Step 'Power'
try {
    # A sleeping machine serves nothing and syncs nothing. The screen may
    # still blank -- there is no screen.
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Good 'never sleeps on mains power'
} catch {
    Problem "could not change the power plan: $($_.Exception.Message)"
}

# ---------------------------------------------------------------- firewall
Step "Phone server firewall rule (port $Port)"
try {
    $name = 'daily-sheet-server'
    Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name $name -DisplayName 'Daily Sheet server' -Direction Inbound `
        -Protocol TCP -LocalPort $Port -Action Allow -Profile Private | Out-Null
    Good "inbound TCP $Port allowed on private networks"
} catch {
    Problem "could not add the firewall rule: $($_.Exception.Message)"
}

# ---------------------------------------------------------------- claude code
if (-not $NoClaudeCode) {
    # The CLI, not the desktop app: this machine has no screen and you will be
    # arriving over SSH. `claude` in the project folder is the whole point --
    # changes get made on the machine rather than pasted at it.
    Step 'Claude Code'
    if (Install-App 'OpenJS.NodeJS.LTS' 'node' 'Node.js LTS') {
        try {
            cmd /c "npm install -g @anthropic-ai/claude-code" | Out-Null
            Update-Path
            if (Have 'claude') {
                Good 'installed -- ssh in, cd to the project, type: claude'
                Todo 'Run "claude" once over SSH and sign in.'
            } else {
                Problem 'npm finished but claude is not on PATH -- open a new shell and check'
            }
        } catch {
            Problem "npm install failed: $($_.Exception.Message)"
        }
    }
}

# ---------------------------------------------------------------- tasks
if (-not $NoTasks) {
    Step 'Scheduled tasks'
    $installer = Join-Path $project 'install-task.ps1'
    if (Test-Path -LiteralPath $installer) {
        try {
            & $installer -At $At -SyncEvery $SyncEvery -Port $Port -Server
            Good "daily at $At, sync every $SyncEvery min, server at logon"
        } catch {
            Problem "install-task.ps1 failed: $($_.Exception.Message)"
        }
    } else {
        Problem 'install-task.ps1 not found -- did the clone work?'
    }
}

# ---------------------------------------------------------------- the rest
if (-not (Test-Path -LiteralPath $envFile) -or
    -not (Get-EnvValue @(Get-Content -LiteralPath $envFile) 'CALENDAR_JSON')) {
    # Only worth saying when the calendar was not found already -- a checklist
    # that lists things you have done teaches you to skim it.
    Todo ('Sign into OneDrive as this user (your work account) and sync ' +
          'Apps/DailySheet, then re-run this script -- it will find calendar.json ' +
          'and fill CALENDAR_JSON in. Signing in needs a screen: Remote Desktop or a monitor, once.')
}
Todo 'Turn on automatic logon for this user. Scheduled tasks and OneDrive both need a logged-on session, and a headless machine has none after a reboot. Sysinternals Autologon stores the password in LSA rather than the registry in clear text.'
Todo 'On the laptop, run ".\install-task.ps1 -Remove" so two machines do not fight over today''s sheet.'

# ---------------------------------------------------------------- verify
Step 'Checking every connection'
if ($havePython -and (Test-Path -LiteralPath (Join-Path $project 'daily_sheet'))) {
    Push-Location $project
    try {
        py -m daily_sheet doctor
    } catch {
        Problem "doctor could not run: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
} else {
    Note 'skipped -- no Python or no project'
}

Write-Host ''
Write-Host '────────────────────────────────────────────────────────' -ForegroundColor DarkGray
if ($problems.Count -gt 0) {
    Write-Host ''
    Write-Host "$($problems.Count) thing(s) did not work:" -ForegroundColor Yellow
    foreach ($p in $problems) { Write-Host "  - $p" -ForegroundColor Yellow }
}

Write-Host ''
Write-Host 'Still needs you, once:' -ForegroundColor White
$i = 1
foreach ($t in $todo) { Write-Host "  $i. $t"; $i++ }

Write-Host ''
Write-Host 'Then, from this machine or your phone:' -ForegroundColor White
Write-Host "  cd $project"
Write-Host '  py -m daily_sheet generate      # build today''s sheet and push it'
Write-Host '  py -m daily_sheet doctor        # check it again after filling in .env'
Write-Host ''
