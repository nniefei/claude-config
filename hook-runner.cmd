@echo off
set CLAUDE_ROOT=%~dp0
pythonw "%CLAUDE_ROOT%scripts\hook-runner.py" %*
