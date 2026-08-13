@echo off
rem Start Menu "CaseClerk Status" entry, and the installer's opt-in
rem "Run caseclerk doctor" finish action: a couple of read-only diagnostic
rem commands, kept open with `pause` since caseclerk.exe is a console app
rem that would otherwise flash and close before anyone could read it.

echo CaseClerk status
echo ================
echo.
"%~dp0caseclerk.exe" doctor
echo.
"%~dp0caseclerk.exe" share status
echo.
pause
