# Ansible Audit Report

**Generated:** 2025-12-20
**Auditor:** Claude Code (Ansible Auditor)
**Scope:** /home/lucid/unrealRenderFarm/ansible/

---

## CRITICAL Issues

### [CRITICAL] setup-autologon.yml:22 - Password stored in registry in plain text

**Problem:** The DefaultPassword registry key stores the admin password unencrypted. Anyone with registry read access can retrieve it.

**Fix:** This is inherent to Windows auto-logon. Add no_log to prevent exposure in logs.

**Example:**
```yaml
    - name: Set auto-logon registry keys
      ansible.windows.win_regedit:
        path: HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon
        name: "{{ item.name }}"
        data: "{{ item.data }}"
        type: string
      loop:
        - { name: 'AutoAdminLogon', data: '1' }
        - { name: 'DefaultUserName', data: 'Administrator' }
        - { name: 'DefaultPassword', data: '{{ ansible_password }}' }
        - { name: 'DefaultDomainName', data: '' }
      no_log: true  # Prevent password from appearing in ansible output
```

---

### [CRITICAL] inventory.yml:28-29 - WinRM over HTTP without encryption

**Problem:** Using port 5985 (HTTP) with cert validation disabled. Credentials transmitted in clear text on the network.

**Fix:** Use HTTPS (port 5986) with proper certificates, or at minimum enable message encryption with CredSSP/Kerberos transport.

**Example:**
```yaml
      vars:
        ansible_connection: winrm
        ansible_winrm_transport: credssp  # or kerberos
        ansible_winrm_server_cert_validation: validate
        ansible_port: 5986
```

---

### [CRITICAL] start-workers.yml:30 - Password embedded in scheduled task

**Problem:** Password stored in scheduled task definition. Visible to anyone with task scheduler access.

**Fix:** Acceptable for render farm isolation, but add no_log and consider using a service account with managed credentials.

**Example:**
```yaml
    - name: Create scheduled task for render worker
      community.windows.win_scheduled_task:
        name: RenderFarmWorker
        # ... rest of config
        password: "{{ ansible_password }}"
      no_log: true
```

---

## WARNING Issues

### [WARNING] sync-repo.yml:10 vs inventory.yml:32 - Inconsistent repo_path

**Problem:** Playbook defines repo_path as `C:\Users\Administrator\unrealRenderFarm` but inventory defines it as `C:\unrealRenderFarm`

**Fix:** Remove local vars override and use the inventory variable.

**Example:**
```yaml
  # Remove lines 9-10 from sync-repo.yml:
  # vars:
  #   repo_path: C:\Users\Administrator\unrealRenderFarm
```

---

### [WARNING] start-workers.yml:13-14 - Same inconsistency with repo_path/uv_path

**Problem:** Hardcoded paths override inventory vars and may not match actual deployment.

**Fix:** Define paths in inventory or group_vars, remove playbook-level overrides.

---

### [WARNING] group_vars/all.yml:7 - Empty password fails silently

**Problem:** If ANSIBLE_WINRM_PASSWORD env var is unset, ansible_password becomes empty string. Playbooks will fail with unclear authentication errors.

**Fix:** Add validation or mandatory lookup.

**Example:**
```yaml
ansible_password: "{{ lookup('env', 'ANSIBLE_WINRM_PASSWORD') | mandatory('ANSIBLE_WINRM_PASSWORD env var must be set') }}"
```

---

### [WARNING] stop-workers.yml:20 - Incorrect changed_when logic

**Problem:** Task always reports "changed" even when no workers were running.

**Fix:** Parse output to determine actual state.

**Example:**
```yaml
      register: stop_result
      changed_when: "'Stopped' in stop_result.stdout"
```

---

### [WARNING] sync-repo.yml:14-16 - Missing idempotency and error handling

**Problem:** Always reports changed. Git pull failures not properly handled.

**Fix:** Add changed_when logic and failed_when for git errors.

**Example:**
```yaml
    - name: Pull latest changes
      ansible.windows.win_shell: |
        cd {{ repo_path }}
        git pull 2>&1
      register: git_result
      changed_when: "'Already up to date' not in git_result.stdout"
      failed_when: git_result.rc != 0 and 'Already up to date' not in git_result.stdout
```

---

### [WARNING] start-workers.yml:40-45 - Tasks always report changed

**Problem:** `changed_when: true` is hardcoded, making playbook output misleading.

**Fix:** Use proper detection logic.

**Example:**
```yaml
    - name: Stop existing task and processes
      ansible.windows.win_shell: |
        $stopped = $false
        if (Get-ScheduledTask -TaskName RenderFarmWorker -ErrorAction SilentlyContinue) {
          Stop-ScheduledTask -TaskName RenderFarmWorker -ErrorAction SilentlyContinue
          $stopped = $true
        }
        $procs = Get-Process -Name python -ErrorAction SilentlyContinue
        if ($procs) { $procs | Stop-Process -Force; $stopped = $true }
        Write-Output $(if ($stopped) { "CHANGED" } else { "OK" })
      register: stop_result
      changed_when: "'CHANGED' in stop_result.stdout"
```

---

## INFO Issues

### [INFO] All playbooks - Missing tags for selective execution

**Problem:** No tags defined, preventing selective task execution.

**Fix:** Add tags for common operations.

**Example:**
```yaml
    - name: Pull latest changes
      ansible.windows.win_shell: git pull
      tags: [sync, git]
```

---

### [INFO] ansible.cfg:3 - host_key_checking disabled

**Problem:** SSH host key verification disabled. Acceptable for internal infrastructure but noted for awareness.

---

### [INFO] setup-autologon.yml:21 - Hardcoded Administrator username

**Problem:** 'Administrator' is hardcoded rather than using ansible_user variable.

**Fix:** Use `{{ ansible_user }}` for consistency.

**Example:**
```yaml
        - { name: 'DefaultUserName', data: '{{ ansible_user }}' }
```

---

### [INFO] status.yml - Could use win_service for future worker service

**Suggestion:** Consider converting worker to a Windows service for better management, then use win_service module for status checks.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3     |
| WARNING  | 6     |
| INFO     | 4     |

## Priority Fixes

1. Add `no_log: true` to all tasks handling passwords
2. Fix inconsistent `repo_path` variable definitions
3. Add `mandatory()` to password lookup
4. Fix `changed_when` logic for accurate playbook output

---

## Files Reviewed

- `ansible.cfg`
- `inventory.yml`
- `group_vars/all.yml`
- `playbooks/status.yml`
- `playbooks/stop-workers.yml`
- `playbooks/sync-repo.yml`
- `playbooks/setup-autologon.yml`
- `playbooks/start-workers.yml`
