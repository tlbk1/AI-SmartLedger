@echo off
chcp 65001 >nul
title wxRecordBot 停止服务
echo 正在停止 wxRecordBot 相关进程...
taskkill /FI "WINDOWTITLE eq wxRecordBot-uvicorn*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq wxRecordBot-cpolar*" /T /F >nul 2>&1
taskkill /IM cpolar.exe /F >nul 2>&1
echo.
echo 已停止。窗口应该都关掉了。
pause
