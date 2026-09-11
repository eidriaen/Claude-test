<#
.SYNOPSIS
    Open a shell on the mini PC.

.DESCRIPTION
    Windows has shipped an SSH client since Windows 10 1809, so there is
    usually nothing to install -- this turns the feature on if it is missing,
    remembers where the mini PC lives, and connects.

    The address and username are kept in %LOCALAPPDATA%\DailySheet\minipc.txt
    rather than in the repo: it is not a secret, but it is yours, and the repo
    is shared. Nothing here stores a password -- SSH asks, or uses your key.

.EXAMPLE
    .\connect.ps1
    .\connect.ps1 -Target minipc
    .\connect.ps1 -Target 100.92.14.3 -User adrian
    .\connect.ps1 -Forget                # ask me again next time
    .\connect.ps1 -Command "py -m daily_sheet doctor"
#>
param(
    [string]$Target = '',
    [string]$User = '',
    [int]$Port = 22,
    [string]$Command = '',
    [switch]$Forget
)

$ErrorActionPreference = 'Stop'

$store = Join-Path $env:LOCALAPPDATA 'DailySheet'
$file  = Join-Path $store 'minipc.txt'

if ($Forget -and (Test-Path -LiteralPath $file)) {
    Remove-Item -LiteralPath $file
    Write-Host 'Forgotten. Run this again to enter a new address.'
    if (-not $Target) { return }
}

# -- the client ---------------------------------------------------------
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host 'Turning on the Windows OpenSSH client...' -ForegroundColor Cyan
    try {
        $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Client*' |
               Where-Object State -ne 'Installed' | Select-Object -First 1
        if ($cap) { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('Path', 'User')
    } catch {
        Write-Host ''
        Write-Host 'Could not enable the SSH client automatically.' -ForegroundColor Yellow
        Write-Host 'Run this window as administrator and try again, or install it from'
        Write-Host 'Settings -> System -> Optional features -> OpenSSH Client.'
        return
    }
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host 'ssh still is not on PATH. Open a new window and try again.' -ForegroundColor Yellow
    return
}

# -- where to ------------------------------------------------------------
$saved = @{}
if ((Test-Path -LiteralPath $file) -and -not $Target) {
    foreach ($line in Get-Content -LiteralPath $file) {
        $k, $v = $line -split '=', 2
        if ($v) { $saved[$k.Trim()] = $v.Trim() }
    }
}

if (-not $Target) { $Target = $saved['host'] }
if (-not $User)   { $User   = $saved['user'] }
if ($saved['port'] -and $Port -eq 22) { $Port = [int]$saved['port'] }

if (-not $Target) {
    Write-Host ''
    Write-Host 'Where is the mini PC?' -ForegroundColor White
    Write-Host 'Its Tailscale name works from anywhere; its local IP only at home.' -ForegroundColor DarkGray
    $Target = (Read-Host 'address').Trim()
    if (-not $Target) { return }
}
if (-not $User) {
    $User = (Read-Host "username on $Target").Trim()
    if (-not $User) { return }
}

New-Item -ItemType Directory -Force -Path $store | Out-Null
"host=$Target`nuser=$User`nport=$Port" | Set-Content -LiteralPath $file -Encoding ASCII

# -- go ------------------------------------------------------------------
Write-Host ''
Write-Host "Connecting to $User@$Target" -ForegroundColor Cyan -NoNewline
if ($Port -ne 22) { Write-Host " on port $Port" -ForegroundColor Cyan } else { Write-Host '' }
Write-Host 'First time, it will ask you to trust the machine''s fingerprint -- say yes.' -ForegroundColor DarkGray
Write-Host ''

# Not $args -- that is an automatic variable, and shadowing it inside a script
# is the kind of thing that works until it suddenly does not.
$sshArgs = @('-p', "$Port", "$User@$Target")
if ($Command) { $sshArgs += $Command }
& ssh @sshArgs
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Host "ssh exited with $code." -ForegroundColor Yellow
    Write-Host 'If it could not connect, check in this order:' -ForegroundColor Yellow
    Write-Host '  1. Is the mini PC on?'
    Write-Host '  2. Is Tailscale up on both machines? (tailscale status)'
    Write-Host "  3. Is the address right? Re-enter it with:  .\connect.ps1 -Forget"
}
