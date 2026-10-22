@echo off
cd /d "%~dp0"
"C:\Users\suisei\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\suisei\.openclaw\workspace\whale\tools\screen_power.py" on >> nap-alarm.log 2>&1
"C:\Users\suisei\AppData\Local\Programs\Python\Python312\python.exe" sunrise_alarm.py --preview 600 --keep >> nap-alarm.log 2>&1
