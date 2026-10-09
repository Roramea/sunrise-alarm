@echo off
rem ================================================================
rem  日出光闹钟 · 快速测试预览（双击就跑）
rem
rem  要试什么就改下面 6 行 → 保存 → 双击本文件
rem  TEST_OVERLAY=1 铺全屏日光层（ESC / Ctrl+Alt+Q 可退出），0 = 只试背光
rem  TEST_HOLD=1 到顶后保持不结束（要手动关），0 = 到顶就结束
rem  整段爬升会被压缩到 TEST_SECONDS 秒内演完，跑完自动把亮度复原
rem ================================================================
set TEST_SECONDS=20
set TEST_FROM=5
set TEST_TO=100
set TEST_CURVE=1.0
set TEST_OVERLAY=1
set TEST_HOLD=0
rem ================================================================

cd /d "%~dp0"
set OVERLAY_ARG=
if "%TEST_OVERLAY%"=="0" set OVERLAY_ARG=--no-overlay
set HOLD_ARG=--no-hold
if "%TEST_HOLD%"=="1" set HOLD_ARG=--hold
echo.
echo [测试] 亮度 %TEST_FROM% -^> %TEST_TO%，%TEST_SECONDS% 秒演完，曲线 %TEST_CURVE%
echo [测试] 日光层 %TEST_OVERLAY%，到顶后保持 %TEST_HOLD%
echo [测试] 中途退出：ESC / Ctrl+Alt+Q / 点一下屏幕，或双击「测试-停止.bat」
echo.
python "%~dp0sunrise_alarm.py" --preview %TEST_SECONDS% --start-level %TEST_FROM% --max-level %TEST_TO% --curve %TEST_CURVE% %OVERLAY_ARG% %HOLD_ARG%
echo.
echo [测试] 结束。完整参数见 README.md
pause
