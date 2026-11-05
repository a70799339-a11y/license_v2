@echo off
rem Bundle NSIS installer (load MSVC env + cargo PATH + github mirror)
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
set PATH=C:\Users\Administrator\.cargo\bin;%PATH%
set TAURI_BUNDLER_TOOLS_GITHUB_MIRROR=https://ghfast.top
cd /d C:\songxiankun\projects\license\client
set RUSTFLAGS=-Cdebuginfo=0
call npx tauri build
