@echo off
chcp 65001
title Monster Hunter Bot (Auto-Restart)
:loop
echo 🦖 Monster Hunter Bot Starting...
echo Time: %time%
python main.py
echo ⚠️ Bot crashed or stopped! Restarting in 5 seconds...
timeout /t 5
goto loop
