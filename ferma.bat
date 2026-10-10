@echo off
chcp 65001 >nul
rem Ferma backend (porta 8000) e dashboard (porta 3000) di QUESTO progetto.
rem Chiude solo processi la cui riga di comando appartiene al progetto: se una porta
rem e' occupata da altro, lo dice e non lo tocca. I conti paper sono salvati su file
rem a ogni ciclo con scrittura atomica: fermare qui non perde posizioni ne' trade.

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$trovati = 0;" ^
  "foreach ($porta in 8000, 3000) {" ^
  "  $ids = Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique;" ^
  "  foreach ($id in $ids) {" ^
  "    $riga = (Get-CimInstance Win32_Process -Filter \"ProcessId=$id\").CommandLine;" ^
  "    if ($riga -match 'ai-trading-agent' -or $riga -match 'backend\.main:app') {" ^
  "      Stop-Process -Id $id -Force; $trovati++;" ^
  "      Write-Host \"Fermato il processo $id sulla porta $porta.\"" ^
  "    } else { Write-Host \"Porta $porta occupata da un altro programma (processo $id): non lo tocco.\" }" ^
  "  }" ^
  "}" ^
  "if ($trovati -eq 0) { Write-Host 'Niente da fermare: backend e dashboard non erano in esecuzione.' }"
echo.
echo Per ripartire: avvia.bat
pause
