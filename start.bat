@echo off
cd /d "%~dp0"
rem First time:  start.bat --receiver 192.168.0.50     (use your receiver's address; it is remembered)
start "" http://localhost:8780
python server.py --lan %*
