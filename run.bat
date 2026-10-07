@echo off
REM Auto-start entry point. Launches both pieces, then exits itself.
REM   - desktop pet: pythonw, so no console window at all
REM   - monitor bot: a MINIMISED console. It needs a real console so it can
REM     hear Windows' shutdown event for the shutdown guard, but /min keeps
REM     it out of your way (it sits in the taskbar).
cd /d "%~dp0"
start "" pythonw desktop_pet.py
start "Laptop Monitor Bot" /min python monitor_bot.py
