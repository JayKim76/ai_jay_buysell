# 쿠팡 스토어 판매부적합 감시 - Windows 작업 스케줄러 30분마다 등록
# 사용법: PowerShell을 관리자 권한으로 열고 이 폴더에서 실행
#   .\setup_store_watch_task.ps1
# 이미 등록된 작업이 있으면 덮어씁니다.

$taskName = "Coupang Store Watch - 30min"
$folder = $PSScriptRoot
if (-not $folder) { $folder = "E:\AI\threads-coupang-poster" }
$batPath = Join-Path $folder "run_store_watch.bat"

if (-not (Test-Path $batPath)) {
    Write-Host "run_store_watch.bat을 찾을 수 없습니다: $batPath" -ForegroundColor Red
    exit 1
}

# 기존 작업 삭제 (있으면)
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "기존 작업을 삭제했습니다."
}

# 30분마다 반복, 시작은 지금부터, 지속 기간 무제한
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$action = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $folder
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Settings $settings -Description "쿠팡 스토어 신규 상품을 30분마다 검사해 판매부적합 의심 상품이 있으면 알림" | Out-Null
Write-Host "등록 완료: $taskName (30분마다, $batPath)"
Write-Host "확인: 작업 스케줄러에서 '$taskName'을 검색하세요."
Write-Host "중지: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
