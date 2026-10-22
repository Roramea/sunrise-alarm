# sunrise-alarm 运维小脚本（给 OpenClaw automation 调用）
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File alarm-ops.ps1 -Action check
#     -> 打印 RAMPING 或 IDLE（state.json 是否存在）
#   powershell -NoProfile -ExecutionPolicy Bypass -File alarm-ops.ps1 -Action restore
#     -> 把 config.json 恢复成日常值（07:00 / 07:30）、把计划任务触发器改回 07:00、并禁用任务
#     -> 打印 RESTORED 或 RESTORE-FAIL: <原因>
param([Parameter(Mandatory=$true)][ValidateSet('check','restore','restore-on')][string]$Action)

$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = Join-Path $dir 'config.json'
$state = Join-Path $dir 'state.json'
$taskName = 'SunriseAlarm'

if ($Action -eq 'check') {
    if (Test-Path $state) { Write-Output 'RAMPING' } else { Write-Output 'IDLE' }
    exit 0
}

if ($Action -eq 'restore' -or $Action -eq 'restore-on') {
    $ok = $true
    # 1) config.json 回到日常
    $json = @'
{
  "start_time": "07:00",
  "end_time": "07:30",
  "start_level": 5,
  "max_level": 100,
  "curve": 1.0,
  "tick_seconds": 5,
  "fade_in_display": true,
  "monitor_index": 0,
  "task_time": "07:00",
  "task_weekdays": [
    "MON",
    "TUE",
    "WED",
    "THU",
    "FRI"
  ]
}
'@
    try { [IO.File]::WriteAllText($cfg, $json, (New-Object Text.UTF8Encoding($false))) } catch { $ok = $false; Write-Output ('RESTORE-FAIL config: ' + $_.Exception.Message) }

    # 2) 触发器回到 07:00，然后禁用（假期里 10/5-10/7 不要亮）
    try {
        $trig = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 7:00AM
        Set-ScheduledTask -TaskName $taskName -Trigger $trig -ErrorAction Stop | Out-Null
    } catch { $ok = $false; Write-Output ('RESTORE-FAIL trigger: ' + $_.Exception.Message) }
    if ($Action -eq 'restore-on') {
        try { Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null } catch { $ok = $false; Write-Output ('RESTORE-FAIL enable: ' + $_.Exception.Message) }
    } else {
        try { Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null } catch { $ok = $false; Write-Output ('RESTORE-FAIL disable: ' + $_.Exception.Message) }
    }

    $tag = if ($Action -eq 'restore-on') { 'RESTORED-ON' } else { 'RESTORED' }
    if ($ok) { Write-Output $tag } else { Write-Output ($tag + '-WITH-WARNINGS') }
    exit 0
}
