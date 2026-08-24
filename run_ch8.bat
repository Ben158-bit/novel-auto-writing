@echo off
rem ============================================================
rem 手动/补跑指定章节（用于巡航失败后的续跑）
rem 用法示例：schtasks /create /tn DeterminFlow-Ch8 /sc once /st 14:40 /tr "D:\DeterminFlow-dy\run_ch8.bat" /f
rem            schtasks /run /tn DeterminFlow-Ch8
rem 也可直接双击/命令行执行本脚本（前台）
rem ============================================================
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

".venv\Scripts\python.exe" pipeline.py --chapters 8 8 >> "logs\run_ch8.log" 2>&1

exit /b %ERRORLEVEL%
