@echo off

echo poop & echo butt

echo Starting test batch file...
echo Current directory: %CD%
echo Current time: %TIME%
echo.

echo Running some test commands:
dir /b
echo.

echo Listing environment variables:
set | findstr "PATH\|USER\|COMP"
echo.

echo Creating a test file:
echo This is a test file > test_output.txt
type test_output.txt
del test_output.txt

echo.
echo Test batch file completed!
pause
