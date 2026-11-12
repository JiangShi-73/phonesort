@echo off
chcp 65001 >nul
title 手机相册分组整理
cd /d "%~dp0"
python -m phonesort gui
if errorlevel 1 pause
