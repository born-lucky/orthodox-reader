# Builds dist\Reader.exe (one file, no console).
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install -q -r requirements.txt pyinstaller
python -m pytest -q tests
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "Reader" `
    --icon assets\icon.ico `
    --add-data "data;data" `
    --collect-data certifi `
    --hidden-import pystray._win32 `
    --hidden-import reader.room --collect-binaries miniaudio `
    --hidden-import win32com.client --hidden-import pythoncom `
    main.py
Write-Host "Built: dist\Reader.exe"
