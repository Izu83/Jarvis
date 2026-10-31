@echo off
rem Start Jarvis. With no arguments it starts hidden: no window, just the tray icon under the ^ arrow in the taskbar
rem (right-click it to restart, open the log or quit). With arguments it runs in this window, e.g.
rem   run.bat --text      type commands here      run.bat --console   see the log live in this window
rem   run.bat --pair      connect your phone      run.bat --email     connect your email
rem   run.bat --keys      API keys for ChatGPT and Grok (pay per use)
rem   run.bat --browser   show Jarvis's hidden browser to log in to a site for him (e.g. Instagram)
cd /d "%~dp0"
if not "%~1"=="" goto here
start "" wscript.exe "%~dp0Jarvis.vbs"
echo Jarvis is starting in the background. Find its icon in the taskbar tray (the ^^ arrow).
exit /b

:here
if /i "%~1"=="--console" shift
".venv\Scripts\python.exe" -m jarvis %1 %2 %3 %4 %5 %6 %7 %8 %9
