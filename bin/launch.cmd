@echo off
rem Convenience wrapper: the MCP row starts run.ps1 directly, but this makes the
rem launcher easy to test from a terminal.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
