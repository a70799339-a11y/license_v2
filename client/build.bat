@echo off
rem 构建/测试客户端（自动加载 MSVC 环境）
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cd /d C:\songxiankun\projects\license\client\src-tauri
set RUSTFLAGS=-Cdebuginfo=0
if "%1"=="test" (
  C:\Users\Administrator\.cargo\bin\cargo.exe test -p license-core
) else if "%1"=="debug" (
  C:\Users\Administrator\.cargo\bin\cargo.exe build
) else if "%1"=="cross" (
  C:\Users\Administrator\.cargo\bin\cargo.exe run -p license-core --example cross_verify -- C:\songxiankun\projects\license\tools\signed_license.json
) else (
  C:\Users\Administrator\.cargo\bin\cargo.exe build --release
)
