@echo off
setlocal
cd /d "%~dp0"

rem Windows Task Scheduler entrypoint. The scheduled CLI freezes the latest
rem source date due at the local 09:30 boundary and records durable status.
rem Arguments such as --model, --effort, --more, or --force pass through.
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0daily_briefing.py" scheduled %*
) else (
  python "%~dp0daily_briefing.py" scheduled %*
)
exit /b %errorlevel%
