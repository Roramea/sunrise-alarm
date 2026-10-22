# 把 config.json 的爬升时间恢复成日常 07:00 -> 07:30（只改时间，不动别的字段）
$ErrorActionPreference = 'Stop'
$cfg = Join-Path $PSScriptRoot 'config.json'
$o = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
$o.start_time = '07:00'
$o.end_time   = '07:30'
$json = $o | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($cfg, $json, (New-Object Text.UTF8Encoding($false)))
Write-Output ('RESTORED ' + $o.start_time + ' -> ' + $o.end_time)
