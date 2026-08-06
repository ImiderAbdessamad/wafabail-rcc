"""Persistance SQLite (stdlib) — dossiers, corrections analystes, sessions.

Volontairement sans ORM : le service tourne en mono-processus derrière uvicorn
et n'a besoin que de quelques tables. Le schéma est appliqué de façon
idempotente au démarrage.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import DB_PATH, PDF_STORAGE_DIR

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dossiers (
    id              TEXT PRIMARY KEY,
    job_id          TEXT,
    client_name     TEXT NOT NULL DEFAULT '',
    ice             TEXT,
    credit_amount   REAL,
    exercice_date   TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    sector          TEXT,
    -- contexte d'instruction
    filename        TEXT,
    pdf_path        TEXT,
    motif           TEXT,
    comment         TEXT,
    decided_by      TEXT,
    decided_at      TEXT,
    -- instantané du résultat RCC (RccAnalysisResult sérialisé)
    result_json     TEXT,
    completeness_pct REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_dossiers_status  ON dossiers(status);
CREATE INDEX IF NOT EXISTS idx_dossiers_created ON dossiers(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dossiers_job ON dossiers(job_id)
    WHERE job_id IS NOT NULL;

-- Piste d'audit : la valeur OCR n'est jamais écrasée, on empile les corrections.
CREATE TABLE IF NOT EXISTS field_overrides (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id      TEXT NOT NULL REFERENCES dossiers(id) ON DELETE CASCADE,
    field_code      TEXT NOT NULL,
    original_value  REAL,
    corrected_value REAL,
    edited_by       TEXT NOT NULL,
    edited_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_overrides_dossier
    ON field_overrides(dossier_id, field_code);
CREATE INDEX IF NOT EXISTS idx_overrides_at
    ON field_overrides(edited_at DESC);

-- Évènements de cycle de vie (validation / rejet / arbitrage / ingestion),
-- affichés dans « Historique & piste d'audit » à côté des corrections.
CREATE TABLE IF NOT EXISTS dossier_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id      TEXT NOT NULL REFERENCES dossiers(id) ON DELETE CASCADE,
    action          TEXT NOT NULL,
    detail          TEXT,
    actor           TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_dossier ON dossier_events(dossier_id);
CREATE INDEX IF NOT EXISTS idx_events_at      ON dossier_events(created_at DESC);

-- Migrations idempotentes (ALTER TABLE n'a pas d'IF NOT EXISTS en SQLite) :
-- voir _migrate().

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


#: Colonnes ajoutées après la première mise en service.
#: (table, colonne, définition SQL)
_MIGRATIONS: list[tuple[str, str, str]] = [
    # Confirmation explicite d'un poste à faible confiance dont la valeur OCR
    # est jugée correcte par l'analyste (sans correction de valeur).
    ("field_overrides", "verified", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.commit()


def init_db() -> None:
    """Crée le schéma si nécessaire, puis applique les migrations. Idempotent."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
        _conn.executescript(_SCHEMA)
        _conn.commit()
        _migrate(_conn)
    Path(PDF_STORAGE_DIR).mkdir(parents=True, exist_ok=True)


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


@contextmanager
def cursor(*, commit: bool = False) -> Iterator[sqlite3.Cursor]:
    """Curseur sérialisé — sqlite3 en mono-connexion partagée.

    Le verrou évite les écritures concurrentes depuis les threads du pool
    FastAPI (les endpoints sync tournent en threadpool).
    """
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
            _conn.executescript(_SCHEMA)
            _migrate(_conn)
        cur = _conn.cursor()
        try:
            yield cur
            if commit:
                _conn.commit()
        except Exception:
            _conn.rollback()
            raise
        finally:
            cur.close()
