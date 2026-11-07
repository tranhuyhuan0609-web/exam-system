@echo off
cd /d "%~dp0"
title Exam System Server (Port 8000 - Ho tro xem tren Dien thoai)
echo ========================================================
echo   DANG KHOI DONG SERVER HE THONG THI (EXAM SYSTEM)
echo   May tinh xem tai: http://localhost:8000
echo   Dien thoai xem tai: http://192.168.1.218:8000
echo   (Luu y: Dien thoai va May tinh ket noi chung mang Wi-Fi)
echo   De dung server: Nhan Ctrl + C
echo ========================================================
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause
