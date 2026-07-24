@echo off
title FTSEHybrid A.I. Dashboard - Port 5042
cd /d C:\Users\abc\Desktop\FTSEHybridAI
start /min "FTSEHybrid A.I. Dashboard" cmd /c C:\Users\abc\AppData\Local\Programs\Python\Python313\python.exe dashboard_ftse.py
timeout /t 5 /nobreak >nul
start http://localhost:5042
