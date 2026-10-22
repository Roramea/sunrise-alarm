@echo off
rem ÈÕ³ö¹âÄÖÖÓ ¡¤ ÍøÒ³°æ GUI£¨pywebview ×ÀÃæ¿Ç£©
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0sunrise_gui_web.py"
) else (
  start "" python "%~dp0sunrise_gui_web.py"
)
exit /b
