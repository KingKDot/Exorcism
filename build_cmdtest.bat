@echo off
setlocal

set "ROOT=%~dp0"
set "MANIFEST=%ROOT%cmdtest\Cargo.toml"
set "SOURCE_DLL=%ROOT%cmdtest\target\release\cmdtest.dll"
set "OUTPUT_DIR=%ROOT%bin"
set "OUTPUT_DLL=%OUTPUT_DIR%\cmdtest.dll"

where cargo >nul 2>nul
if errorlevel 1 (
    echo error: cargo was not found in PATH.
    echo Install Rust from https://rustup.rs/ and try again.
    exit /b 1
)

echo Building Rust hook DLL...
cargo build --manifest-path "%MANIFEST%" --release
if errorlevel 1 (
    echo error: Rust hook DLL build failed.
    exit /b 1
)

if not exist "%SOURCE_DLL%" (
    echo error: expected DLL was not produced:
    echo   "%SOURCE_DLL%"
    exit /b 1
)

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"
if errorlevel 1 (
    echo error: failed to create output directory:
    echo   "%OUTPUT_DIR%"
    exit /b 1
)

copy /Y "%SOURCE_DLL%" "%OUTPUT_DLL%" >nul
if errorlevel 1 (
    echo error: failed to copy DLL to:
    echo   "%OUTPUT_DLL%"
    exit /b 1
)

echo Hook DLL ready:
echo   "%OUTPUT_DLL%"
exit /b 0
