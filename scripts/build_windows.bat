@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name TelegramMediaSuite --collect-submodules pyrogram app\web_app.py
copy /y .env.example dist\TelegramMediaSuite\.env.example >nul
 echo Built dist\TelegramMediaSuite\TelegramMediaSuite.exe
pause
