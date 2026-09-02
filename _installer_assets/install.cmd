@echo off
chcp 65001 >nul
setlocal

echo ============================================
echo   板检数据核验 Agent - 一键安装
echo ============================================
echo.

REM ---- 默认安装目录(免管理员权限)----
set "DEST=%LOCALAPPDATA%\板检数据核验Agent"

REM ---- 复制全部文件到目标目录 ----
if not exist "%DEST%" mkdir "%DEST%"
xcopy /E /I /Y "%~dp0*" "%DEST%" >nul 2>&1

echo 已安装到: %DEST%
echo.

REM ---- 创建桌面快捷方式 ----
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\板检数据核验Agent.lnk');" ^
  "$s.TargetPath='%DEST%\板检数据核验Agent.exe';" ^
  "$s.WorkingDirectory='%DEST%';" ^
  "$s.Description='板检数据核验 Agent';" ^
  "$s.Save()" >nul 2>&1

REM ---- 启动主程序 ----
echo 正在启动板检数据核验 Agent...
start "" "%DEST%\板检数据核验Agent.exe"

echo.
echo 安装完成!桌面已创建快捷方式。
echo 浏览器将自动打开核验页面 http://127.0.0.1:5050
echo.
pause
