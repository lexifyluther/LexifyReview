@echo off
chcp 65001 >nul
title LexifyReview

:: Check if installed
if not exist "venv\Scripts\activate.bat" (
    echo Chưa cài đặt! Đang chạy trình cài đặt...
    call LexifyReview_Install.bat
)

:: Activate venv and run
call venv\Scripts\activate.bat
start /b pythonw shopify_reviews_gui.py
