@echo off
chcp 65001 > nul
title Đồng bộ Dữ liệu Premier League từ Hugging Face Hub về Local

echo ===============================================================================
echo   ĐỒNG BỘ DỮ LIỆU NGOẠI HẠNG ANH TỪ HUGGING FACE VỀ LOCAL DATA
echo   Repository: https://huggingface.co/datasets/Toibinguz/epl-premier-league-dataset
echo ===============================================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [LỖI] Không tìm thấy Python trong hệ thống PATH!
    echo Vui lòng cài đặt Python hoặc thêm Python vào biến môi trường PATH.
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"

echo [*] Đang kiểm tra thư viện và tiến hành tải bộ dữ liệu...
python sync_from_hf.py

if %ERRORLEVEL% equ 0 (
    echo.
    echo [THÀNH CÔNG] Dữ liệu đã được đồng bộ toàn vẹn về thư mục data/!
) else (
    echo.
    echo [CẢNH BÁO] Quá trình đồng bộ gặp sự cố. Vui lòng kiểm tra kết nối mạng hoặc token.
)

echo.
echo ===============================================================================
echo   Bấm phím bất kỳ để đóng cửa sổ này...
echo ===============================================================================
pause > nul
