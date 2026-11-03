"""SQLite access layer: schema, connection helper and catalog seeding."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    country TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bikes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    model TEXT NOT NULL,
    category TEXT NOT NULL,
    engine_cc INTEGER,              -- NULL for electric bikes
    fuel TEXT NOT NULL DEFAULT 'petrol',
    approx_price_inr INTEGER NOT NULL,
    UNIQUE (brand_id, model)
);

-- One pool per (bike, city): buyers of the same bike in the same city
-- can realistically buy from the same dealer.
CREATE TABLE IF NOT EXISTS pools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bike_id INTEGER NOT NULL REFERENCES bikes(id),
    city TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','negotiating','deal_secured','closed')),
    target_size INTEGER NOT NULL DEFAULT 10,
    dealer_name TEXT,
    discount_inr INTEGER,
    deal_valid_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (bike_id, city)
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_id INTEGER NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT,
    timeline TEXT NOT NULL DEFAULT '1_month',
    secret TEXT NOT NULL,           -- returned once on join; lets a buyer manage their own entry
    fee_status TEXT NOT NULL DEFAULT 'not_due'
        CHECK (fee_status IN ('not_due','pending','paid')),
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (pool_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_bikes_brand ON bikes(brand_id);
CREATE INDEX IF NOT EXISTS idx_members_pool ON members(pool_id);
"""

SEED_FILE = Path(__file__).parent / "data" / "bikes.json"


def connect() -> sqlite3.Connection:
    path = get_settings().db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and seed the catalog (idempotent)."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        seed_catalog(conn)


def seed_catalog(conn: sqlite3.Connection) -> None:
    catalog = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    for brand in catalog["brands"]:
        conn.execute(
            "INSERT OR IGNORE INTO brands (slug, name, country) VALUES (?, ?, ?)",
            (brand["slug"], brand["name"], brand["country"]),
        )
        brand_id = conn.execute("SELECT id FROM brands WHERE slug = ?", (brand["slug"],)).fetchone()["id"]
        for bike in brand["bikes"]:
            conn.execute(
                """INSERT OR IGNORE INTO bikes
                   (brand_id, model, category, engine_cc, fuel, approx_price_inr)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    brand_id,
                    bike["model"],
                    bike["category"],
                    bike.get("cc"),
                    bike.get("fuel", "petrol"),
                    bike["price"],
                ),
            )
