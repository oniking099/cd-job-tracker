@echo off
chcp 65001 >nul
title Refresh BOSS Cookie
cd /d "%~dp0"

echo ==========================================================
echo   BOSS QR login + auto-update GitHub secret (BOSS_COOKIE)
echo   Usage : double-click, scan QR with BOSS app, then done
echo   Note  : cookie expires ~1 day; refresh daily before 17:00
echo ==========================================================
echo.

REM Must use the Windows Python launcher (C:\Python313) which has
REM playwright+camoufox. conda base python does NOT -- it lacks them.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYRUN=py"
) else (
    set "PYRUN=C:\Python313\python.exe"
)

echo [START] Running refresh_boss_session.py via %PYRUN% ...
echo (A browser will open. Scan the QR with your BOSS app to log in.)
echo.
%PYRUN% scripts\refresh_boss_session.py
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [OK] Done: session exported and BOSS_COOKIE secret updated.
    echo       No more action needed today. The 17:00 search will use it.
) else (
    echo [FAIL] Exit code %RC%. See logs above.
    echo        Retry by double-clicking this file again.
    echo        Manual fallback: paste .sessions\boss_cookie.json
    echo        into the GitHub secret BOSS_COOKIE.
)
echo.
pause
