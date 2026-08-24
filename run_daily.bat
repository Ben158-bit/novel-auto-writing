@echo off
rem ============================================================
rem DeterminFlow 每日自动产出任务
rem 由 Windows 计划任务触发（默认每天 19:00），产出 2 章正文
rem 并写批次报告到 logs\pipeline_YYYYMMDD.log
rem ============================================================
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

".venv\Scripts\python.exe" pipeline.py --daily 2 >> "logs\run_daily.log" 2>&1

exit /b %ERRORLEVEL%
