# WinRM Setup for Ansible
# Run this ONCE on each Windows render node (as Administrator)
# Based on: https://docs.ansible.com/ansible/latest/os_guide/windows_winrm.html

$ErrorActionPreference = "Stop"

Write-Host "Configuring WinRM for Ansible..." -ForegroundColor Cyan

# Enable WinRM
Write-Host "Enabling WinRM service..."
Set-Service -Name WinRM -StartupType Automatic
Start-Service WinRM

# Configure WinRM
Write-Host "Running winrm quickconfig..."
winrm quickconfig -quiet

# Enable NTLM authentication
Write-Host "Enabling NTLM authentication..."
Set-Item -Path WSMan:\localhost\Service\Auth\Basic -Value $false
Set-Item -Path WSMan:\localhost\Service\Auth\Negotiate -Value $true

# Allow unencrypted for local network (optional, remove for production)
Set-Item -Path WSMan:\localhost\Service\AllowUnencrypted -Value $true

# Set LocalAccountTokenFilterPolicy for non-domain admin access
Write-Host "Setting LocalAccountTokenFilterPolicy..."
$regPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
Set-ItemProperty -Path $regPath -Name LocalAccountTokenFilterPolicy -Value 1 -Type DWord

# Configure firewall
Write-Host "Configuring firewall rules..."
$firewallParams = @{
    DisplayName = "WinRM HTTP"
    Direction = "Inbound"
    LocalPort = 5985
    Protocol = "TCP"
    Action = "Allow"
}
New-NetFirewallRule @firewallParams -ErrorAction SilentlyContinue

# Increase MaxMemoryPerShellMB for large operations
Set-Item -Path WSMan:\localhost\Shell\MaxMemoryPerShellMB -Value 2048

# Test WinRM
Write-Host "`nTesting WinRM configuration..." -ForegroundColor Cyan
winrm enumerate winrm/config/listener

Write-Host "`n✓ WinRM configured successfully!" -ForegroundColor Green
Write-Host "Test from Ansible control node with: ansible render_nodes -m win_ping" -ForegroundColor Yellow
