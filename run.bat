@echo off
rem Start Jarvis. Extra arguments are passed through, e.g.  run.bat --text
cd /d "%~dp0"
".venv\Scripts\python.exe" -m jarvis %*
