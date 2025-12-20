# Render Farm Ansible Setup

Manage all 8 render nodes from one place.

## Prerequisites

### Control Node (Linux Server)

```bash
# Install Ansible
sudo apt install ansible

# Install pywinrm for Windows support
pipx inject ansible "pywinrm>=0.4.0"
# Or if using pip:
pip install "pywinrm>=0.4.0"
```

### Windows Render Nodes (One-time Setup)

Run this **once** on each Windows VM as Administrator:

```powershell
# Run the bootstrap script (enables SSH + WinRM + firewall)
cd C:\Users\Administrator\unrealRenderFarm
git pull
powershell -ExecutionPolicy Bypass -File .\ansible\scripts\setup-windows.ps1
```

## Configuration

1. Edit `inventory.yml` - update IP addresses for your VMs
2. Set credentials:

```bash
export ANSIBLE_WINRM_USER=Administrator
export ANSIBLE_WINRM_PASSWORD=yourpassword
```

## Usage

```bash
cd ansible

# Test connectivity to all nodes
ansible render_nodes -m win_ping

# Start workers on all nodes
ansible-playbook playbooks/start-workers.yml

# Stop all workers
ansible-playbook playbooks/stop-workers.yml

# Check status of all nodes
ansible-playbook playbooks/status.yml

# Pull latest code on all nodes
ansible-playbook playbooks/sync-repo.yml

# Run on specific nodes
ansible-playbook playbooks/start-workers.yml --limit render-node-01,render-node-02
```

## Troubleshooting

**Connection refused:**
- Check Windows firewall allows port 5985
- Verify WinRM service is running: `Get-Service WinRM`

**Authentication failed:**
- Check credentials are correct
- Verify `LocalAccountTokenFilterPolicy` is set to 1

**Test WinRM from Windows:**
```powershell
winrm enumerate winrm/config/listener
Test-WSMan -ComputerName localhost
```

## References

- [Ansible Windows Remote Management](https://docs.ansible.com/ansible/latest/os_guide/windows_winrm.html)
- [Setting up Windows Hosts](https://docs.ansible.com/ansible/latest/os_guide/intro_windows.html)
