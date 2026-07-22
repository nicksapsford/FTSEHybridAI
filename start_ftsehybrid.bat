@echo off
title FTSEHybrid A.I. - Port 5042
cd /d C:\Users\abc\Desktop\FTSEHybridAI
start /min "FTSEHybrid A.I. Dashboard" cmd /c C:\Users\abc\AppData\Local\Programs\Python\Python313\python.exe dashboard_ftse.py
start /min "FTSEHybrid A.I. Engine" cmd /c C:\Users\abc\AppData\Local\Programs\Python\Python313\python.exe watchdog_ftse.py
