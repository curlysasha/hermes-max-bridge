# Создаёт ярлыки автозагрузки для Hermes Desktop и локальной модели Qwen.
$ws = New-Object -ComObject WScript.Shell
$startup = [Environment]::GetFolderPath('Startup')

# 1) Hermes Desktop (GUI-приложение)
$p1 = Join-Path $startup 'Hermes Desktop.lnk'
$l1 = $ws.CreateShortcut($p1)
$l1.TargetPath       = 'C:\Users\user\AppData\Local\Programs\hermes-desktop\hermes-agent.exe'
$l1.WorkingDirectory = 'C:\Users\user\AppData\Local\Programs\hermes-desktop'
$l1.Description      = 'Hermes Desktop (autostart)'
$l1.Save()
Write-Host "ok: $p1"

# 2) Локальная модель Qwen3.6-27B (llama-server), свёрнутым окном
$p2 = Join-Path $startup 'Qwen Local Model.lnk'
$l2 = $ws.CreateShortcut($p2)
$l2.TargetPath       = 'C:\llama-b9553-bin-win-cuda-12.4-x64\run-qwen36-27b.bat'
$l2.WorkingDirectory = 'C:\llama-b9553-bin-win-cuda-12.4-x64'
$l2.WindowStyle      = 7   # minimized
$l2.Description      = 'Qwen3.6-27B local model server (autostart)'
$l2.Save()
Write-Host "ok: $p2"
