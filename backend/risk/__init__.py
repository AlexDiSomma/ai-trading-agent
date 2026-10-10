"""Risk Engine: deterministico e indipendente dall'AI.

Verdetti possibili: APPROVED, REDUCED_SIZE, REJECTED, WAIT. Nessun prompt e
nessun output del modello possono aggirarlo; non importa nulla da `ai`.
Contiene budget giornaliero, position sizing e circuit breaker. Fase 6.
"""
