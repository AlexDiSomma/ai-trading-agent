@echo off
chcp 65001 >nul
rem Avvia l'MVP PAPER: backend (con scheduler) e dashboard.
rem Il terminale MetaTrader 5 deve essere aperto e collegato al conto XM:
rem non gira in Docker (vedi docs/adr/0003-mt5-gateway-windows.md).
rem PostgreSQL e Redis sono facoltativi: i conti paper stanno in var\paper.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] Ambiente Python assente. Esegui prima:
  echo     python -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -e ".[mt5,ai,dev]"
  pause
  exit /b 1
)
if not exist ".env" (
  echo [!] File .env assente. Copia .env.example in .env e compilalo.
  pause
  exit /b 1
)
if not exist "frontend\node_modules" (
  echo === Dipendenze della dashboard ===
  pushd frontend
  call npm install
  popd
)
if not exist "frontend\.env.local" (
  echo [!] frontend\.env.local assente: copia frontend\.env.local.example e metti in
  echo     API_TOKEN lo stesso valore di API_AUTH_TOKEN del file .env.
  pause
  exit /b 1
)

rem Una copia gia' in esecuzione occupa le porte: il nuovo avvio fallirebbe senza dirlo.
netstat -ano | findstr /r /c:"127.0.0.1:8000 .*LISTENING" /c:"127.0.0.1:3000 .*LISTENING" >nul
if not errorlevel 1 (
  echo [!] Backend o dashboard gia' in esecuzione: porte 8000 o 3000 occupate.
  echo     Per riavviare: esegui ferma.bat, poi di nuovo avvia.bat.
  pause
  exit /b 1
)

findstr /b /c:"DASHBOARD_PASSWORD_HASH=scrypt:" "frontend\.env.local" >nul
if errorlevel 1 (
  echo === Accesso alla dashboard ===
  echo     Primo avvio: scegli nome utente e password per entrare nella dashboard.
  pushd frontend
  call npm run imposta-password
  popd
  findstr /b /c:"DASHBOARD_PASSWORD_HASH=scrypt:" "frontend\.env.local" >nul
  if errorlevel 1 (
    echo [!] Credenziali non impostate: la dashboard resterebbe chiusa.
    pause
    exit /b 1
  )
)

echo === PostgreSQL e Redis (facoltativi) ===
docker compose up -d postgres redis >nul 2>&1
if errorlevel 1 (
  echo     Docker non disponibile: si prosegue senza. System Health lo segnala.
) else (
  .venv\Scripts\python.exe -m alembic upgrade head
)

echo.
echo === Dashboard ===
rem Versione di produzione: piu' veloce. La build rispecchia sempre il codice attuale.
start "AI Trading Agent - dashboard" cmd /k "cd /d %~dp0frontend && npm run build && npm run start"

echo === Backend + scheduler PAPER ===
echo     Dashboard      http://localhost:3000  (con nome utente e password)
echo     API            http://127.0.0.1:8000/docs
echo     Chiudere questa finestra ferma lo scheduler (i conti paper restano salvati).
echo     Per fermare tutto, anche la dashboard: ferma.bat
echo.
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
