@echo off
rem Build/test client (auto-load MSVC env). ASCII-only: cmd reads this file in system codepage.
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cd /d C:\songxiankun\projects\license\client\src-tauri
set RUSTFLAGS=-Cdebuginfo=0
if "%1"=="test" (
  C:\Users\Administrator\.cargo\bin\cargo.exe test -p license-core
) else if "%1"=="alltest" (
  rem Full unit tests: license-core + main.rs (set_server validation cases since M2)
  C:\Users\Administrator\.cargo\bin\cargo.exe test
) else if "%1"=="debug" (
  C:\Users\Administrator\.cargo\bin\cargo.exe build
) else if "%1"=="cross" (
  C:\Users\Administrator\.cargo\bin\cargo.exe run -p license-core --example cross_verify -- C:\songxiankun\projects\license\tools\signed_license.json
) else (
  C:\Users\Administrator\.cargo\bin\cargo.exe build --release
)
