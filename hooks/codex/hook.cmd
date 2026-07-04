@echo off
setlocal
set "AGENT=%~1"
if /I "%AGENT%"=="claude" goto agent_ok
if /I "%AGENT%"=="codex" goto agent_ok
set "AGENT=unknown"
:agent_ok
node "%~dp0hook-forwarder.js" %AGENT%
exit /b 0
