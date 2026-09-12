"""SQLite-Zugriff für den Band Manager.

Eine Datei, kein ORM: das Schema ist klein (Musiker, Gigs, Varianten, Posten)
und ändert sich selten. Migrationen laufen über `schema_version`.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(os.getenv("BANDMANAGER_DB", Path.home() / "band-manager-data" / "band-manager.db"))

FLAGS = ("open", "done", "na")
GIG_STATUS = ("vorlage", "angebot", "bestaetigt", "abgerechnet", "abgesagt")
ITEM_KINDS = ("musician", "cost")
# Nur diese Status zählen als „gespielt/geplant" für Auswertungen und offene Zahlungen
COUNTING_STATUS = ("bestaetigt", "abgerechnet")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS musicians (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role        TEXT NOT NULL DEFAULT '',
    default_fee INTEGER NOT NULL DEFAULT 0,
    email       TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    iban        TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS gigs (
    id                INTEGER PRIMARY KEY,
    title             TEXT NOT NULL,
    date              TEXT,                     -- ISO yyyy-mm-dd oder NULL
    venue             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'angebot'
                      CHECK (status IN ('vorlage','angebot','bestaetigt','abgerechnet','abgesagt')),
    notes             TEXT NOT NULL DEFAULT '',
    active_variant_id INTEGER,
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS variants (
    id         INTEGER PRIMARY KEY,
    gig_id     INTEGER NOT NULL REFERENCES gigs(id) ON DELETE CASCADE,
    name       TEXT NOT NULL DEFAULT 'Standard',
    fee        INTEGER NOT NULL DEFAULT 0,      -- Gage netto, ganze Euro
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS line_items (
    id          INTEGER PRIMARY KEY,
    variant_id  INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'musician' CHECK (kind IN ('musician','cost')),
    role        TEXT NOT NULL DEFAULT '',
    musician_id INTEGER REFERENCES musicians(id) ON DELETE SET NULL,
    label       TEXT NOT NULL DEFAULT '',
    amount      INTEGER NOT NULL DEFAULT 0,
    info        TEXT NOT NULL DEFAULT 'open' CHECK (info IN ('open','done','na')),
    invoice     TEXT NOT NULL DEFAULT 'open' CHECK (invoice IN ('open','done','na')),
    paid        TEXT NOT NULL DEFAULT 'open' CHECK (paid IN ('open','done','na')),
    note        TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Protokoll: wann wurde wer worüber informiert / erinnert (für MCP-Erinnerungen)
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    gig_id   INTEGER REFERENCES gigs(id) ON DELETE CASCADE,
    item_id  INTEGER REFERENCES line_items(id) ON DELETE SET NULL,
    ts       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    kind     TEXT NOT NULL,                     -- reminder | status | note
    text     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_variants_gig ON variants(gig_id);
CREATE INDEX IF NOT EXISTS idx_items_variant ON line_items(variant_id);
CREATE INDEX IF NOT EXISTS idx_items_musician ON line_items(musician_id);
CREATE INDEX IF NOT EXISTS idx_events_gig ON events(gig_id);
"""

SCHEMA_VERSION = 1


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Eine Transaktion pro Request/Tool-Aufruf. Commit bei Erfolg, Rollback bei Fehler."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
