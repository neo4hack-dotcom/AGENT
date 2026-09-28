@echo off
:: AGENT - developpement sous Windows (equivalent de `make api` + `make web`).
:: Deux fenetres : l'API sur :3041, l'interface sur :3040 (qui relaie /api vers :3041).
::
:: L'API tourne SANS --reload : sous Windows, uvicorn --reload passe la boucle asyncio en
:: mode Selector, qui ne sait pas lancer de sous-processus - ni run_python ni aucun serveur
:: MCP local ne demarreraient. Apres une modification du backend, relancez sa fenetre.
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
    echo [ERREUR] Lancez d'abord install-windows.bat.
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo [ERREUR] Lancez d'abord install-windows.bat.
    exit /b 1
)
set PYTHONUTF8=1
start "AGENT API :3041" /D "%~dp0backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --port 3041"
start "AGENT web :3040" /D "%~dp0frontend" cmd /k "npm run dev"
echo [AGENT] API       http://localhost:3041
echo [AGENT] Interface http://localhost:3040
timeout /t 5 /nobreak >nul
start "" "http://localhost:3040"
endlocal
