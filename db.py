"""SQLite store for this app's own small state - nothing about Tableau's content
(workbooks, custom views) is cached here; that's always fetched live (see
tableau_client.py). Two tables:

- app_config: key/value store for the encrypted PAT and passcode hash, same shape as
  tableau-admin-dashboard's db.py.
- account_numbers: email -> account_number cache, kept warm by a background BigQuery
  sync (see bigquery_sync.py) so a compare's custom-view owners can be annotated with
  their account number without a live BigQuery query on every request.
"""
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS account_numbers (
    email TEXT PRIMARY KEY,
    account_number TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# --- app_config -------------------------------------------------------------

def set_config(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_config(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_config(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def is_setup_complete() -> bool:
    return get_config("pat_encrypted") is not None and get_config("passcode_hash") is not None


# --- account_numbers ----------------------------------------------------------

def upsert_account_numbers(mapping: dict, synced_at: str):
    """Bulk upsert the full email -> account_number map fetched from BigQuery."""
    if not mapping:
        return
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO account_numbers(email, account_number, synced_at) VALUES (?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET account_number = excluded.account_number, "
            "synced_at = excluded.synced_at",
            [(email.lower().strip(), account_number, synced_at) for email, account_number in mapping.items()],
        )


def get_account_numbers(emails: list) -> dict:
    """Case-insensitive lookup for a specific list of owner emails - returns only the
    ones found, keyed by the ORIGINAL (not lowercased) email as passed in."""
    if not emails:
        return {}
    with get_conn() as conn:
        placeholders = ",".join("?" for _ in emails)
        lowered = [e.lower().strip() for e in emails]
        rows = conn.execute(
            f"SELECT email, account_number FROM account_numbers WHERE email IN ({placeholders})",
            lowered,
        ).fetchall()
        by_lower = {row["email"]: row["account_number"] for row in rows}
        return {e: by_lower[e.lower().strip()] for e in emails if e.lower().strip() in by_lower}
