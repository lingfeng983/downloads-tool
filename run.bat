@echo off
cd /d "%~dp0"
python -m downloader
if errorlevel 1 pause
