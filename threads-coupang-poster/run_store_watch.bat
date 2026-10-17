@echo off
REM 쿠팡 스토어 판매부적합 감시 - 30분마다 작업 스케줄러에서 실행
REM 시작 위치: E:\AI\threads-coupang-poster
REM .env에 쿠팡 API 키가 있어야 합니다. 키는 이 파일에 넣지 마세요.

cd /d "%~dp0"
python store_watch.py --check-once --status ALL >> store_watch_run.log 2>&1

REM 알림이 있으면 Windows 알림 표시 (alerts.log에 새 항목이 생겼을 때)
REM store_watch.py가 alerts.log에 기록하면 아래 PowerShell이 토스트를 띄웁니다.
REM 최근 5분 내 알림이 있으면 토스트 표시
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$log='store_watch_alerts.log';" ^
  "if (Test-Path $log) { $last=(Get-Item $log).LastWriteTime; if ((Get-Date)-$last -lt [TimeSpan]::FromMinutes(5)) { $lines=Get-Content $log -Tail 3; $msg=$lines -join \"`n\"; try { [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; $template=[Windows.UI.Notifications.ToastTemplateType]::ToastText02; $xml=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template); $xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('쿠팡 부적합상품 알림')) | Out-Null; $xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode($msg.Substring(0, [Math]::Min(120, $msg.Length)))) | Out-Null; $toast=[Windows.UI.Notifications.ToastNotification]::new($xml); [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Coupang Store Watch').Show($toast) } catch { Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show($msg, '쿠팡 부적합상품 알림') } } }"
