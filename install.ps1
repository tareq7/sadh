# Sadh Windows installer: download to temporary files and verify the release SHA-256.
# Review before execution. Usage: powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"

$Repo = "tareq7/sadh"
$InstallDir = Join-Path $env:LOCALAPPDATA "Sadh"
$TargetExe = Join-Path $InstallDir "Sadh.exe"
$BaseUrl = "https://github.com/$Repo/releases/latest/download"
$TempExe = Join-Path $env:TEMP ("Sadh-" + [guid]::NewGuid().ToString("N") + ".exe")
$TempSums = Join-Path $env:TEMP ("Sadh-" + [guid]::NewGuid().ToString("N") + ".sha256")
$BackupExe = Join-Path $InstallDir "Sadh.exe.previous"

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

try {
    Write-Host "[1/4] Downloading release binary and checksums..."
    Invoke-WebRequest -Uri "$BaseUrl/Sadh-windows-x64.exe" -OutFile $TempExe -UseBasicParsing
    Invoke-WebRequest -Uri "$BaseUrl/SHA256SUMS.txt" -OutFile $TempSums -UseBasicParsing

    $Match = [regex]::Match((Get-Content -Raw -Path $TempSums),
        '(?im)^([a-f0-9]{64})\s+\*?Sadh-windows-x64\.exe\s*$')
    if (-not $Match.Success) {
        throw "The release checksum file does not list Sadh-windows-x64.exe."
    }
    $Expected = $Match.Groups[1].Value.ToUpperInvariant()
    $Actual = (Get-FileHash -Path $TempExe -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($Actual -ne $Expected) {
        throw "SHA-256 mismatch. Existing installation has not been modified."
    }

    Write-Host "[2/4] Installing verified executable..."
    if (Test-Path $BackupExe) { Remove-Item -Path $BackupExe -Force }
    $HadPrevious = Test-Path $TargetExe
    if ($HadPrevious) { Move-Item -Path $TargetExe -Destination $BackupExe }
    try {
        Move-Item -Path $TempExe -Destination $TargetExe
    } catch {
        if ($HadPrevious -and (Test-Path $BackupExe)) {
            Move-Item -Path $BackupExe -Destination $TargetExe
        }
        throw
    }
    if (Test-Path $BackupExe) { Remove-Item -Path $BackupExe -Force }

    Write-Host "[3/4] Creating shortcut and initial configuration..."
    $Shell = New-Object -ComObject WScript.Shell
    $Desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop)
    $Shortcut = $Shell.CreateShortcut((Join-Path $Desktop "Sadh Voice Typing.lnk"))
    $Shortcut.TargetPath = $TargetExe
    $Shortcut.WorkingDirectory = $InstallDir
    $Shortcut.Description = "Sadh Voice Typing"
    $Shortcut.Save()

    $ConfigPath = Join-Path $InstallDir "config.json"
    if (-not (Test-Path $ConfigPath)) {
        @{
            groq_api_key = ""
            model = "whisper-large-v3-turbo"
            language = "auto"
            dialect = "gazan"
            hotkey = "<cmd>+h"
            dictation_mode = "toggle"
            auto_paste = $true
            auto_fix_obvious = $true
            reliable_chunking = $true
            live_typing = $true
            start_with_windows = $true
        } | ConvertTo-Json -Depth 4 | Set-Content -Path $ConfigPath -Encoding UTF8
    }

    Write-Host "[4/4] Launching Sadh..."
    Start-Process -FilePath $TargetExe -WorkingDirectory $InstallDir
    Write-Host "Sadh installation complete."
} finally {
    Remove-Item -Path $TempExe, $TempSums -Force -ErrorAction SilentlyContinue
}
