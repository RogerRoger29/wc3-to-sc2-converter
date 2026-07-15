@echo off
echo Killing stale Python processes...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM blender.exe >nul 2>&1
echo Done.
