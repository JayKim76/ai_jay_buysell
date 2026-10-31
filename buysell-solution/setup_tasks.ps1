# 작업 스케줄러 등록: 30분 동기화 + 매일 09:00 아침 요약
# PowerShell에서 이 파일이 있는 폴더로 이동한 뒤 실행하세요.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

$syncAction = New-ScheduledTaskAction -Execute "$dir\run_sync.bat" -WorkingDirectory $dir
$syncTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "Buysell Sync - 30min" -Action $syncAction -Trigger $syncTrigger -Force

$morningAction = New-ScheduledTaskAction -Execute "$dir\run_morning.bat" -WorkingDirectory $dir
$morningTrigger = New-ScheduledTaskTrigger -Daily -At 09:00
Register-ScheduledTask -TaskName "Buysell Morning Summary - 09:00" -Action $morningAction -Trigger $morningTrigger -Force

Write-Host "등록 완료: 'Buysell Sync - 30min', 'Buysell Morning Summary - 09:00'"
