# Installs the anti-theft failed-login alert.
#
# What it does (all one-time, needs Administrator):
#   1. Turns on auditing of failed logons, so Windows writes event 4625 when
#      a PIN or password is refused.
#   2. Registers a Scheduled Task that runs as SYSTEM and is triggered by that
#      event. Because it is event-triggered, it is armed from boot -- it fires
#      even at the lock screen with nobody logged in, which is the whole point.
#   3. Shows you any failed logons already in the log, so you can confirm what
#      a wrong PIN on THIS machine actually records.
#
# Run it from an elevated PowerShell. The easy way, from a normal PowerShell:
#   Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy','Bypass','-File','D:\Mointor\install_antitheft.ps1'
# Accept the UAC prompt.

$ErrorActionPreference = 'Stop'

# Log everything to a file so a non-interactive caller can read the outcome.
try { Start-Transcript -Path 'D:\Mointor\antitheft_install.log' -Force | Out-Null } catch {}

# --- must be elevated ---
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "This must run as Administrator." -ForegroundColor Red
    Write-Host "From a normal PowerShell, run:"
    Write-Host "  Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy','Bypass','-File','D:\Mointor\install_antitheft.ps1'"
    exit 1
}

$TaskName = 'LaptopMonitor-FailedLogin'
$Dir      = 'D:\Mointor'
$Pyw      = 'C:\Python314\pythonw.exe'
$Script   = Join-Path $Dir 'antitheft_sentinel.py'

if (-not (Test-Path $Pyw))    { throw "pythonw not found at $Pyw" }
if (-not (Test-Path $Script)) { throw "sentinel not found at $Script" }

# --- 1. enable failed-logon auditing ---
Write-Host "`n[1/3] Enabling failed-logon auditing..." -ForegroundColor Cyan
& auditpol /set /subcategory:"Logon" /failure:enable | Out-Null
& auditpol /set /subcategory:"Logon" /success:enable | Out-Null
$state = (& auditpol /get /subcategory:"Logon") -join "`n"
Write-Host $state

# --- 2. register the event-triggered task ---
Write-Host "`n[2/3] Registering scheduled task '$TaskName'..." -ForegroundColor Cyan

# Fire on 4625 (failed logon) or 4776 (credential validation failed).
$subscription = @'
<QueryList><Query Id="0" Path="Security"><Select Path="Security">*[System[(EventID=4625 or EventID=4776)]]</Select></Query></QueryList>
'@

$class = Get-CimClass MSFT_TaskEventTrigger root/Microsoft/Windows/TaskScheduler
$trigger = New-CimInstance -CimClass $class -ClientOnly
$trigger.Enabled = $true
$trigger.Subscription = $subscription

$action = New-ScheduledTaskAction -Execute $Pyw `
    -Argument "`"$Script`" once" -WorkingDirectory $Dir

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
    -LogonType ServiceAccount -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances Queue -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Trigger $trigger -Action $action `
    -Principal $principal -Settings $settings `
    -Description 'Anti-theft: alert on Telegram when a login fails' | Out-Null
Write-Host "Task registered (runs as SYSTEM, armed from boot)." -ForegroundColor Green

# --- 3. show what's already in the log ---
Write-Host "`n[3/3] Failed logons already recorded (last 24h):" -ForegroundColor Cyan
try {
    $ev = Get-WinEvent -FilterHashtable @{
        LogName='Security'; Id=4625,4776; StartTime=(Get-Date).AddHours(-24)
    } -ErrorAction Stop
    if ($ev) {
        $ev | Select-Object -First 10 TimeCreated, Id,
            @{n='Account';e={$_.Properties[5].Value}} | Format-Table -AutoSize
    } else {
        Write-Host "  none in the last 24h (that's fine)."
    }
} catch {
    Write-Host "  couldn't read them: $($_.Exception.Message.Split([char]10)[0])"
}

Write-Host "`nDone." -ForegroundColor Green
Write-Host "TEST IT: lock the screen (Win+L), type a WRONG pin/password once,"
Write-Host "then log in properly. You should get a Telegram alert within a few"
Write-Host "seconds. If you don't, tell Claude what the table above shows."
try { Stop-Transcript | Out-Null } catch {}
