-- Estensioni usate dallo schema. Lo schema vero e proprio e' gestito da Alembic:
-- qui solo cio' che richiede privilegi di superuser.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "btree_gin";
