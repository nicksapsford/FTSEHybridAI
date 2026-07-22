@echo off
title FTSEHybrid AI -- Service Mode
cd /d %~dp0

echo Starting FTSEHybrid AI in service mode (Task Scheduler)...

echo Cleaning up any existing FTSEHybrid processes...
powershell -NoProfile -Command "Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like '*dashboard_ftse.py*' -or $_.CommandLine -like '*watchdog_ftse.py*' -or $_.CommandLine -like '*main_ftsehybrid.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" > nul 2>&1
ping -n 3 127.0.0.1 > nul

start /B python dashboard_ftse.py

ping -n 11 127.0.0.1 > nul

start /B python watchdog_ftse.py

echo FTSEHybrid AI launched in background -- dashboard + watchdog running.
exit /b 0
