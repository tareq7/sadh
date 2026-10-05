# Sadh (صَدْح) — Windows 1-Click Installer
# Usage: irm https://raw.githubusercontent.com/tareq7/sadh/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "   Sadh (صَدْح) — Voice Typing for Windows" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan

$InstallDir = Join-Path $env:LOCALAPPDATA "Sadh"
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

$Repo = "tareq7/sadh"
$ExeUrl = "https://github.com/$Repo/releases/latest/download/Sadh-windows-x64.exe"
$TargetExe = Join-Path $InstallDir "Sadh.exe"

Write-Host "[1/4] Downloading latest Sadh standalone executable..." -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri $ExeUrl -OutFile $TargetExe -UseBasicParsing
    Write-Host "      Downloaded to $TargetExe" -ForegroundColor Green
} catch {
    Write-Warning "Failed to download release binary directly: $_"
    Write-Host "      Please visit https://github.com/$Repo/releases to download manually." -ForegroundColor Red
    exit 1
}

Write-Host "[2/4] Setting up Desktop shortcut..." -ForegroundColor Yellow
try {
    $WshShell = New-Object -ComObject WScript.Shell
    $DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
    $ShortcutPath = Join-Path $DesktopPath "Sadh Voice Typing.lnk"
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetExe
    $Shortcut.WorkingDirectory = $InstallDir
    $Shortcut.Description = "Sadh (صَدْح) Voice Typing"
    $Shortcut.Save()
    Write-Host "      Created $ShortcutPath" -ForegroundColor Green
} catch {
    Write-Warning "Could not create desktop shortcut: $_"
}

Write-Host "[3/4] Initializing local configuration..." -ForegroundColor Yellow
$ConfigPath = Join-Path $InstallDir "config.json"
if (-not (Test-Path $ConfigPath)) {
    $DefaultConfig = @{
        "groq_api_key" = ""
        "model" = "whisper-large-v3-turbo"
        "language" = "auto"
        "dialect" = "gazan"
        "hotkey" = "<cmd>+h"
        "dictation_mode" = "toggle"
        "auto_paste" = $true
        "auto_fix_obvious" = $true
        "reliable_chunking" = $true
        "live_typing" = $true
        "start_with_windows" = $true
    } | ConvertTo-Json -Depth 4
    Set-Content -Path $ConfigPath -Value $DefaultConfig -Encoding UTF8
    Write-Host "      Created initial config at $ConfigPath" -ForegroundColor Green
}

Write-Host "[4/4] Launching Sadh..." -ForegroundColor Yellow
Start-Process -FilePath $TargetExe -WorkingDirectory $InstallDir

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "   Installation Complete! صَدْح جاهز للاستخدام" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "• Press Win + H to toggle voice typing" -ForegroundColor White
Write-Host "• Press Insert to Push-to-Talk" -ForegroundColor White
Write-Host "• Right-click tray icon to set your Groq API key" -ForegroundColor White
Write-Host ""
