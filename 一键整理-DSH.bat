@echo off
chcp 65001 >nul
title 手机相册整理（DSH API）
cd /d "%~dp0"

echo ============================================
echo  手机相册整理 · DSH 视觉分类
echo  手机保持连接、解锁、选“传输文件 / MTP”
echo ============================================
echo.

echo [1/2] 复制手机文件（跳过相机和已分好类的相册）...
start "" /b python progress_watch.py
python -m phonesort run --no-ai --out 整理结果
if errorlevel 1 goto error

echo.
echo [2/2] DSH 视觉分类（32 并发）...
python dsh_reclassify.py --workers 32
if errorlevel 1 goto error

echo.
echo 全部完成。结果在 整理结果 文件夹。
pause
exit /b 0

:error
echo.
echo 出错了，请把上面的日志截图。
pause
exit /b 1
