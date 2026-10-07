# Removes the anti-theft failed-login task. Run as Administrator.
$ErrorActionPreference = 'SilentlyContinue'
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "Run this as Administrator." -ForegroundColor Red; exit 1 }

Unregister-ScheduledTask -TaskName 'LaptopMonitor-FailedLogin' -Confirm:$false
Write-Host "Removed the failed-login task." -ForegroundColor Green
Write-Host "(Auditing was left on. To turn it off:"
Write-Host "  auditpol /set /subcategory:\"Logon\" /failure:disable )"
