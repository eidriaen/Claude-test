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
        # Match the architecture explicitly. The releases page lists an arm64
        # Windows build right next to the Intel one, and picking that gives
        # "This app can't run on your PC" with nothing to say why.
        $asset = $rel.assets |
            Where-Object { $_.name -match '(?i)windows' -and
                           $_.name -match '(?i)(x86_64|amd64|intel)' -and
                           $_.name -match '(?i)\.zip$' } |
            Select-Object -First 1
        if (-not $asset) { throw 'no Windows x86_64 zip in the latest release' }

        $zip = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
        $dir = Join-Path $env:TEMP 'rmapi-unzip'
        Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive $zip -DestinationPath $dir -Force
        $exe = Get-ChildItem $dir -Filter 'rmapi.exe' -Recurse | Select-Object -First 1
        if (-not $exe) { throw 'no rmapi.exe inside the zip' }
        Copy-Item $exe.FullName $rmapi -Force
        Good "installed $($asset.name) to $rmapi"
    } catch {
        Problem "could not fetch rmapi: $($_.Exception.Message)"
        Todo 'Download the Windows x86_64 rmapi from https://github.com/ddvk/rmapi/releases and put rmapi.exe in C:\tools.'
    }
}
Todo 'Run "C:\tools\rmapi.exe" once and paste the code from https://my.remarkable.com/device/desktop/connect. Check the page shows the right account first. SSH is fine for this.'

# ---------------------------------------------------------------- .env
Step 'Settings file (.env)'
$envFile = Join-Path $project '.env'
if (Test-Path -LiteralPath $envFile) {
    Good '.env already exists -- left untouched'
} elseif (Test-Path -LiteralPath (Join-Path $project '.env.example')) {
    Copy-Item (Join-Path $project '.env.example') $envFile
    $token = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
    # Fill in what a machine can know; the secrets are the human's job.
    (Get-Content $envFile) `
        -replace '^WEB_TOKEN=.*', "WEB_TOKEN=$token" `
        -replace '^RMAPI_BIN=.*', "RMAPI_BIN=$rmapi" |
        Set-Content $envFile -Encoding ASCII
    Good "created with a fixed WEB_TOKEN ($token)"
    Todo "Put your ASANA_PAT, CALENDAR_JSON and ANTHROPIC_API_KEY into $envFile."
} else {
    Problem 'no .env.example found -- did the clone work?'
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
Todo 'Sign into OneDrive as this user and let it sync /Apps/DailySheet, then set CALENDAR_JSON in .env to the local path. Needs a screen -- use Remote Desktop.'
Todo 'Point your Power Automate flow at this machine''s OneDrive folder (same flow, same account -- nothing to rebuild).'
Todo 'Turn on automatic logon for this user. Scheduled tasks and OneDrive both need a logged-on session, and a headless machine has none after a reboot. Sysinternals Autologon stores the password in LSA rather than the registry in clear text.'
Todo 'On the laptop, run ".\install-task.ps1 -Remove" so two machines do not fight over today''s sheet.'

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
Write-Host 'Then check it:' -ForegroundColor White
Write-Host "  cd $project"
Write-Host '  py -m daily_sheet doctor'
Write-Host ''
