@echo off
rem Starts WhisperType in the background (tray icon, no console window).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0whispertype.py"
