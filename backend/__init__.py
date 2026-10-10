"""AI Trading Agent - backend.

La struttura dei package rispecchia i bounded context descritti in
docs/BOUNDED_CONTEXTS.md: l'ordine della pipeline (mondo -> mercato -> asset ->
portafoglio -> rischio -> esecuzione) e' visibile nelle dipendenze fra moduli,
non solo nei prompt dell'AI.
"""

__version__ = "0.1.0"
