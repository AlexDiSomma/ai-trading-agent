"""MVP operativo in PAPER: piani deterministici, esecuzione simulata, gestione, P/L.

Nessun modulo di questo package invia ordini al broker. Dal broker si leggono
prezzi e si chiedono calcoli (profitto, size) tramite `BrokerValuationService`;
le posizioni esistono solo nel conto paper (`ledger`).
"""
