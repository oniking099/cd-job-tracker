@echo off
chcp 65001 >nul
title 刷新 BOSS 登录 Cookie
cd /d "%~dp0"

echo ==========================================================
echo    BOSS 扫码登录 + 自动更新 GitHub secret（BOSS_COOKIE）
echo    用法：双击本文件 → 手机 BOSS App 扫码 → 自动完成
echo    注意：cookie 约一天过期，刷新后当天 17:00 检索才有效
echo ==========================================================
echo.

REM 必须用 Windows Python 启动器（C:\Python313），它装了 playwright+camoufox
REM 别用 conda base 的 python —— (base) 环境没装这些依赖，会报 ModuleNotFoundError
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYRUN=py"
) else (
    set "PYRUN=C:\Python313\python.exe"
)

echo [开始] 正在用 %PYRUN% 运行 refresh_boss_session.py ...
echo （浏览器弹出后，用手机 BOSS App 扫码登录；登录成功会自动继续）
echo.
%PYRUN% scripts\refresh_boss_session.py
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo ✅ 全部完成：登录态已导出，BOSS_COOKIE secret 已更新。
    echo    今天不用再操作了，17:00 检索会自动用新 cookie 拉 BOSS。
) else (
    echo ❌ 未完成（退出码 %RC%）。
    echo    常见原因：扫码没成功 / 上传 secret 失败 —— 看上面日志定位。
    echo    处理：直接再双击本文件重试即可；或手动打开
    echo    .sessions\boss_cookie.json 粘贴到 GitHub secret BOSS_COOKIE。
)
echo.
pause
