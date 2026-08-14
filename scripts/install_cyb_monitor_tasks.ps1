param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$closeTaskName = 'CYB Signal Monitor Close 15-30'
$closeBat = Join-Path $ProjectRoot 'run_cyb_signal_monitor_close.bat'

if (-not (Test-Path -LiteralPath $closeBat -PathType Leaf)) {
    throw "Required launcher does not exist: $closeBat"
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
$arguments = '/d /c "set CYB_SCHEDULED=1&&call ""' + $closeBat + '"""'
$action = New-ScheduledTaskAction `
    -Execute $cmdExe `
    -Argument $arguments `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At '15:30'
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Prompt for today 0AMV close and send CYB close signal email (weekdays 15:30).'
Register-ScheduledTask -TaskName $closeTaskName -InputObject $task -Force | Out-Null

foreach ($oldName in @(
    'CYB Signal Monitor Intraday 14-40',
    'CYB Sync Compass 0AMV 15-35',
    'CYB Signal Monitor Close 15-45',
    'CYB Signal Monitor Close 15-20',
    'CYB Signal Monitor 14-40',
    'AI金融-同步指南针0AMV'
)) {
    $oldTask = Get-ScheduledTask -TaskName $oldName -ErrorAction SilentlyContinue
    if ($null -ne $oldTask) {
        Unregister-ScheduledTask -TaskName $oldName -Confirm:$false
    }
}

$task = Get-ScheduledTask -TaskName $closeTaskName
$info = Get-ScheduledTaskInfo -TaskName $closeTaskName
[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    UserId = $task.Principal.UserId
    LogonType = $task.Principal.LogonType
    NextRunTime = $info.NextRunTime
    Execute = $task.Actions.Execute
    Arguments = $task.Actions.Arguments
    WorkingDirectory = $task.Actions.WorkingDirectory
}
