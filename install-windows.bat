@echo off
:: AGENT - installation sous Windows (equivalent de `make install`).
:: A lancer une fois, depuis n'importe quel dossier : double-clic ou `install-windows.bat`.
:: Hors ligne : pip et npm passent par les miroirs internes declares dans pip.ini / .npmrc.
setlocal EnableExtensions
cd /d "%~dp0"
echo [AGENT] Installation pour Windows...

:: --- Python 3.12 ou plus : le lanceur py d'abord, puis python sur le PATH ---
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PY=%%~C"
    )
)
if not defined PY (
    echo [ERREUR] Python 3.12 ou plus est introuvable.
    echo          Installez-le depuis le catalogue logiciel de l'entreprise, avec le lanceur "py".
    exit /b 1
)
echo [OK] Python : %PY%

where npm >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Node.js 20 ou plus est introuvable ^(npm absent du PATH^).
    exit /b 1
)

:: --- Chemins longs Windows : node_modules et site-packages depassent vite 260 caracteres ---
reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2>nul | find "0x1" >nul
if errorlevel 1 (
    echo [AGENT] Activation des chemins longs Windows...
    reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f >nul 2>&1
    if errorlevel 1 (
        echo [AVERTISSEMENT] Droits administrateur requis pour les chemins longs.
        echo                 Relancez ce script en tant qu'Administrateur si des erreurs persistent.
    ) else (
        echo [OK] Chemins longs actives.
    )
) else (
    echo [OK] Chemins longs deja actives.
)

:: --- Backend : environnement virtuel et dependances ---
if not exist "backend\.venv\Scripts\python.exe" (
    echo [AGENT] Creation de backend\.venv...
    %PY% -m venv backend\.venv
    if errorlevel 1 (
        echo [ERREUR] Creation de l'environnement virtuel impossible.
        exit /b 1
    )
)
set "VPY=%~dp0backend\.venv\Scripts\python.exe"
"%VPY%" -m pip install -q --upgrade pip
"%VPY%" -m pip install -q -r backend\requirements.txt
if errorlevel 1 (
    echo [ERREUR] pip install a echoue. Hors ligne, verifiez le miroir PyPI interne dans pip.ini.
    exit /b 1
)
echo [OK] Dependances Python installees.

:: --- Frontend : cache npm court, pour eviter les chemins trop longs ---
set "NPM_CACHE=%SystemDrive%\npm-cache"
pushd frontend
call npm install --cache "%NPM_CACHE%" --no-audit --no-fund
if errorlevel 1 (
    echo.
    echo [AGENT] npm install a echoue. Nouvel essai apres verification du cache...
    call npm cache verify --cache "%NPM_CACHE%" >nul 2>&1
    call npm install --cache "%NPM_CACHE%" --no-audit --no-fund
    if errorlevel 1 (
        popd
        echo [ERREUR] npm install a echoue. Hors ligne, verifiez le miroir npm interne dans .npmrc.
        exit /b 1
    )
)
popd
echo [OK] Dependances de l'interface installees.

if not exist "backend\.env" (
    copy /y ".env.example" "backend\.env" >nul
    echo [OK] backend\.env cree a partir de .env.example.
)

echo.
echo [OK] Installation terminee.
echo      Developpement : start-windows.bat   ^(API :3041, interface :3040^)
echo      Production    : serve-windows.bat   ^(un seul processus sur :3041^)
endlocal
