@echo off
cd /d "%~dp0"
title H2Edu - AI Import Worker
echo ========================================================
echo   TIEN TRINH XU LY NHAP DE THI TU DONG (AI WORKER)
echo   He thong su dung ket hop 5 Gemini Keys + 3 Groq Keys
echo   De dung tien trinh: Nhan Ctrl + C
echo ========================================================
.\.venv\Scripts\python.exe -m app.worker
pause

