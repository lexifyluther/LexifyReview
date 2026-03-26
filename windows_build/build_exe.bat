@echo off
chcp 65001 >nul
title LexifyReview - Build EXE

echo ============================================================
echo   Build LexifyReview.exe (standalone)
echo   Chạy script này trên máy Windows
echo ============================================================
echo.

:: Activate venv
if not exist "venv\Scripts\activate.bat" (
    echo Chưa cài đặt! Chạy LexifyReview_Install.bat trước.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

:: Install PyInstaller
pip install pyinstaller >nul 2>&1

:: Find Chromium path for bundling
echo [1/3] Tìm Chromium browser...
for /f "delims=" %%i in ('python -c "import subprocess; r=subprocess.run(['python','-m','playwright','install','--dry-run','chromium'],capture_output=True,text=True); print(r.stdout.strip())"') do set CHROMIUM_INFO=%%i

:: Get playwright browsers path
for /f "delims=" %%i in ('python -c "from pathlib import Path; import playwright; p=Path(playwright.__file__).parent / 'driver' / 'package' / '.local-browsers'; print(p if p.exists() else '')"') do set BROWSERS_PATH=%%i

if "%BROWSERS_PATH%"=="" (
    for /f "delims=" %%i in ('python -c "import os; print(os.path.join(os.environ.get('LOCALAPPDATA',''), 'ms-playwright'))"') do set BROWSERS_PATH=%%i
)

echo    Browsers path: %BROWSERS_PATH%
echo.

:: Build with PyInstaller
echo [2/3] Building EXE (vài phút)...
pyinstaller --onedir ^
    --name LexifyReview ^
    --windowed ^
    --noconfirm ^
    --clean ^
    --add-data "shopify_reviews_gui.py;." ^
    --hidden-import playwright ^
    --hidden-import playwright.sync_api ^
    --hidden-import playwright_stealth ^
    --collect-all playwright ^
    --collect-all playwright_stealth ^
    lexifyreview_launcher.py

echo.

:: Copy Chromium browsers into dist
echo [3/3] Đóng gói Chromium browser...
if exist "%BROWSERS_PATH%" (
    xcopy /E /I /Y "%BROWSERS_PATH%" "dist\LexifyReview\_playwright_browsers" >nul
    echo    ✅ Đã đóng gói Chromium
) else (
    echo    ⚠️  Không tìm thấy Chromium. User cần chạy: playwright install chromium
)

echo.
echo ============================================================
echo   ✅ BUILD HOÀN TẤT!
echo   File: dist\LexifyReview\LexifyReview.exe
echo   Nén thư mục dist\LexifyReview để chia sẻ.
echo ============================================================
echo.
pause
