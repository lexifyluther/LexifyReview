@echo off
chcp 65001 >nul
title LexifyReview - Cài đặt

echo ============================================================
echo   LexifyReview - Shopify App Store Review Scraper
echo   Trình cài đặt cho Windows
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [LỖI] Không tìm thấy Python!
    echo.
    echo Hãy tải Python từ: https://www.python.org/downloads/
    echo [QUAN TRỌNG] Tick chọn "Add Python to PATH" khi cài đặt!
    echo.
    pause
    start https://www.python.org/downloads/
    exit /b 1
)

echo [1/4] Đã tìm thấy Python:
python --version
echo.

:: Create venv
echo [2/4] Tạo môi trường ảo...
if not exist "venv" (
    python -m venv venv
)
call venv\Scripts\activate.bat
echo.

:: Install dependencies
echo [3/4] Cài đặt thư viện...
pip install --upgrade pip >nul 2>&1
pip install playwright playwright-stealth
echo.

:: Install Chromium
echo [4/4] Tải trình duyệt Chromium (lần đầu sẽ mất vài phút)...
python -m playwright install chromium
echo.

echo ============================================================
echo   ✅ CÀI ĐẶT HOÀN TẤT!
echo   Chạy file "LexifyReview.bat" để mở ứng dụng.
echo ============================================================
echo.
pause
