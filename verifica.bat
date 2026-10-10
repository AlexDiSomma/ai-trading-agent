@echo off
chcp 65001 >nul
rem Verifica rapida: configurazione, connessione a MT5, lettura del conto.
rem Non invia nessun ordine: legge configurazione, terminale e conto (sola lettura).

cd /d "%~dp0"
.venv\Scripts\python.exe -m backend.verifica
pause
