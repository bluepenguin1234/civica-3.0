@echo off
title Civica Towns - local server  (keep this window open while viewing)
cd /d "%~dp0docs"
echo.
echo   Starting Civica Towns...
echo   The site will open in your browser in a moment.
echo.
echo   Keep this window open while you browse.
echo   Close it (or press Ctrl+C) when you're done.
echo.
start "" http://localhost:8090/
python -m http.server 8090
