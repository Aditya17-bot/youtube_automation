@echo off
REM Unattended daily run, driven by Windows Task Scheduler.
REM
REM Must run with "Run only when user is logged on": image generation needs the
REM interactive GPU session and produces black frames without it.
REM
REM The plan comes from cadence.per_week in each channel config, so this file
REM never needs editing to change what runs when - see: python -m core.schedule
REM
REM No preflight here on purpose. A missed 09:00 run starts at login, which is
REM also when Claude Code npm-installs its own update and its binary briefly
REM stops existing. The wait for that lives in daily.py, where it can reach the
REM log and the failure email; a batch loop here could only exit quietly.
cd /d C:\adi\youtube_auto
.venv\Scripts\python.exe daily.py
exit /b %ERRORLEVEL%
