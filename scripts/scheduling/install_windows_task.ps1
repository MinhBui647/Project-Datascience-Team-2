# Preview or install the daily revision monitor or automatic ingestion task.
# Existing invocations remain read-only; select -Mode AutoIngest explicitly.
[CmdletBinding()]
param(
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$At = '19:00',
    [string]$TaskName = 'EPL Raw Storage - Safe Revision Check',
    [ValidateSet('Check', 'AutoIngest')]
    [string]$Mode = 'Check',
    [switch]$Install,
    [switch]$Replace
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $root 'venv\Scripts\python.exe'
$wrapper = Join-Path $PSScriptRoot 'run_windows_task.ps1'
$module = if ($Mode -eq 'AutoIngest') { 'auto_ingest' } else { 'unified_incremental_runner' }
$runner = Join-Path $root "scripts\orchestration\$module.py"
if (!(Test-Path -LiteralPath $python)) { throw 'Python venv not found' }
if (!(Test-Path -LiteralPath $runner)) { throw 'Runner not found' }
if (!(Test-Path -LiteralPath $wrapper)) { throw 'Scheduled-task wrapper not found' }
$timeValue = [datetime]::ParseExact($At, 'HH:mm', [cultureinfo]::InvariantCulture)
Write-Host "Task name: $TaskName"
Write-Host "Time (Windows local timezone $([TimeZoneInfo]::Local.Id)): $At"
Write-Host "Project directory: $root"
Write-Host "Python: $python"
if ($Mode -eq 'AutoIngest') {
    Write-Host 'Mode: AutoIngest; detect, ingest, verify and finalize a pinned new revision'
} else {
    Write-Host 'Mode: Check; read-only revision monitor'
}
Write-Host "Runner: scripts.orchestration.$module"
Write-Host "Logs: $(Join-Path $root 'reports\scheduled_tasks')"
Write-Host 'Requires a logged-in Windows session; missed runs start when Windows is available'
Write-Host 'Overlapping task instances are ignored; automatic scheduler retries are disabled'
if ($Replace) { Write-Host 'Existing task with this name will be replaced when -Install is used' }
if (!$Install) {
    Write-Host 'PREVIEW ONLY: add -Install to register the task.'
    return
}
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and !$Replace) {
    throw 'Task already exists; use -Replace -Install to update it after reviewing the preview.'
}
if ($null -ne $existing -and $existing.State -eq 'Running') {
    throw 'Task is running; wait for it to finish before replacing the registration.'
}
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Mode {1}' -f $wrapper, $Mode
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $timeValue
$executionLimit = if ($Mode -eq 'AutoIngest') { New-TimeSpan -Hours 12 } else { New-TimeSpan -Minutes 20 }
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit $executionLimit -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force:$Replace | Out-Null
Write-Host "TASK REGISTRATION: PASS ($Mode)"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
