@echo off
rem Builds struct_check.c for x64 and writes expected_x64.txt next to this file.
rem The program only prints sizeof/offsetof/constant values; it opens no device.
rem Requires Visual Studio Build Tools 2022 (C++) and the Windows SDK.
rem Override the toolchain location with VCVARS64=<path to vcvars64.bat>.
setlocal
set "HERE=%~dp0"
if not defined VCVARS64 set "VCVARS64=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS64%" goto :novc
call "%VCVARS64%" >nul || exit /b 1
if not exist "%HERE%build" mkdir "%HERE%build"
cl /nologo /W4 /WX /TC "%HERE%struct_check.c" /Fo"%HERE%build\\" /Fe"%HERE%build\struct_check.exe" || exit /b 1
echo # sdk = %WindowsSDKVersion:\=%> "%HERE%expected_x64.txt"
"%HERE%build\struct_check.exe" >> "%HERE%expected_x64.txt" || exit /b 1
echo wrote %HERE%expected_x64.txt
exit /b 0

:novc
echo vcvars64.bat not found - set VCVARS64 to its full path.
exit /b 1
