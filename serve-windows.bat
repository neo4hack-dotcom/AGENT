@echo off
:: AGENT - production sous Windows (equivalent de `make serve`).
:: Construit l'interface puis sert l'API et l'interface depuis un seul processus sur :3041.
:: Pas de --reload, pour la meme raison que dans start-windows.bat.
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
    echo [ERREUR] Lancez d'abord install-windows.bat.
    exit /b 1
)
if "%~1"=="--no-build" goto run
pushd frontend
call npm run build
if errorlevel 1 (
    popd
    echo [ERREUR] La construction de l'interface a echoue.
    exit /b 1
)
popd
:run
set PYTHONUTF8=1
cd /d "%~dp0backend"
echo [AGENT] http://localhost:3041   ^(Ctrl+C pour arreter^)
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 3041
endlocal
