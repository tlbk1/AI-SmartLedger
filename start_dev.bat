@echo off
chcp 65001 >nul
title wxRecordBot 开发环境 - 微信记账机器人
echo ============================================
echo   wxRecordBot 一键启动
echo   微信记账机器人 开发环境
echo ============================================
echo.

cd /d "%~dp0"

echo [1/2] 启动本地服务 (uvicorn :8000) ...
start "wxRecordBot-uvicorn" cmd /k ".venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info"

echo [2/2] 启动内网穿透 (cpolar) ...
start "wxRecordBot-cpolar" cmd /k "E:\cpolar\cpolar.exe http 8000"

echo.
echo ============================================
echo   已启动两个窗口：
echo     wxRecordBot-uvicorn  本地服务
echo     wxRecordBot-cpolar   内网穿透
echo.
echo   cpolar 窗口里会显示公网地址 (https://xxx.cpolar.top)
echo   如果地址变了，记得去微信测试号后台更新 URL
echo ============================================
echo.
pause
