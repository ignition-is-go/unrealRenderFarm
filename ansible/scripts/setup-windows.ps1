# Windows Render Node Bootstrap
# Run as Administrator on each Windows VM
# Enables SSH + WinRM for Ansible management

$ErrorActionPreference = "Stop"

Write-Host "=== Render Node Setup ===" -ForegroundColor Cyan

# --- SSH Setup ---
Write-Host "`n[1/5] Enabling OpenSSH Server..." -ForegroundColor Yellow
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

# --- WinRM Setup ---
Write-Host "[2/5] Enabling WinRM..." -ForegroundColor Yellow
Set-Service -Name WinRM -StartupType Automatic
Start-Service WinRM
winrm quickconfig -quiet 2>$null
Set-Item WSMan:\localhost\Service\Auth\Negotiate -Value $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name LocalAccountTokenFilterPolicy -Value 1 -Type DWord

# --- Network Profile ---
Write-Host "[3/5] Setting network to Private..." -ForegroundColor Yellow
Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private

# --- Firewall Rules ---
Write-Host "[4/5] Configuring firewall..." -ForegroundColor Yellow

# SSH
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Any -ErrorAction SilentlyContinue

# WinRM
New-NetFirewallRule -Name 'WinRM-HTTP-In-TCP' -DisplayName 'WinRM HTTP' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 5985 -Profile Any -ErrorAction SilentlyContinue

# Ping
New-NetFirewallRule -DisplayName "Allow ICMPv4" -Protocol ICMPv4 -IcmpType 8 -Action Allow -Direction Inbound -Profile Any -ErrorAction SilentlyContinue

# --- Verify ---
Write-Host "[5/5] Verifying..." -ForegroundColor Yellow

$sshd = Get-Service sshd
$winrm = Get-Service WinRM
$sshPort = netstat -an | Select-String ":22.*LISTENING"
$winrmPort = netstat -an | Select-String ":5985.*LISTENING"

Write-Host "`n=== Status ===" -ForegroundColor Cyan
Write-Host "SSH:   $($sshd.Status) $(if($sshPort){'(port 22 listening)'}else{'(port 22 NOT listening)'})"
Write-Host "WinRM: $($winrm.Status) $(if($winrmPort){'(port 5985 listening)'}else{'(port 5985 NOT listening)'})"
Write-Host "IP:    $((Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'}).IPAddress -join ', ')"

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host "Test from Ansible control node:"
Write-Host "  ssh Administrator@<this-ip>"
Write-Host "  ansible render_nodes -m win_ping"
