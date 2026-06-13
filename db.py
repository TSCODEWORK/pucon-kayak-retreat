"""
DatabaseClient — SQLite primary store for Pucon Kayak Retreat.

Column names intentionally match the Google Sheets headers so app.py
needs no changes when swapping SheetsClient for DatabaseClient.
"""

import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List

# Canonical column names (match Sheets headers exactly)
INVENTORY_HEADERS = [
    "Item ID", "Category", "Name/Description", "Size", "Status",
    "Condition Notes", "Hourly Rate", "Half-Day Rate", "Full-Day Rate",
    "Multi-Day Rate",
]
RESERVATION_HEADERS = [
    "Reservation ID", "Customer Name", "Customer Phone", "Customer Email",
    "Item IDs", "Start Date & Time", "End Date & Time", "Rental Type",
    "Payment Status", "Payment Amount", "Waiver Signed", "Reservation Status",
    "Notes", "Created At",
]


class DatabaseError(Exception):
    pass


class DatabaseClient:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("PKR_DB_PATH", ".")
        self._db_file = str(Path(db_path) / "rental.db")
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._cache_ts: dict = {}
        self._cache_ttl = 5  # seconds (short — SQLite is local, no reason for long TTL)
        self._init_db()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:  # type: ignore[return]
        conn = sqlite3.connect(self._db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS inventory (
                    "Item ID"          TEXT PRIMARY KEY,
                    "Category"         TEXT DEFAULT '',
                    "Name/Description" TEXT DEFAULT '',
                    "Size"             TEXT DEFAULT '',
                    "Status"           TEXT DEFAULT 'Available',
                    "Condition Notes"  TEXT DEFAULT '',
                    "Hourly Rate"      TEXT DEFAULT '',
                    "Half-Day Rate"    TEXT DEFAULT '',
                    "Full-Day Rate"    TEXT DEFAULT '',
                    "Multi-Day Rate"   TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    "Reservation ID"    TEXT PRIMARY KEY,
                    "Customer Name"     TEXT DEFAULT '',
                    "Customer Phone"    TEXT DEFAULT '',
                    "Customer Email"    TEXT DEFAULT '',
                    "Item IDs"          TEXT DEFAULT '',
                    "Start Date & Time" TEXT DEFAULT '',
                    "End Date & Time"   TEXT DEFAULT '',
                    "Rental Type"       TEXT DEFAULT '',
                    "Payment Status"    TEXT DEFAULT 'Unpaid',
                    "Payment Amount"    TEXT DEFAULT '0',
                    "Waiver Signed"     TEXT DEFAULT 'No',
                    "Reservation Status" TEXT DEFAULT 'Upcoming',
                    "Notes"             TEXT DEFAULT '',
                    "Created At"        TEXT DEFAULT ''
                );
            """)

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _cached(self, key, fetch_fn, force_refresh=False):
        if not force_refresh and key in self._cache:
            if time.time() - self._cache_ts.get(key, 0) < self._cache_ttl:
                return self._cache[key]
        data = fetch_fn()
        self._cache[key] = data
        self._cache_ts[key] = time.time()
        return data

    def clear_cache(self):
        self._cache.clear()
        self._cache_ts.clear()

    # ── Inventory ─────────────────────────────────────────────────────────────

    def get_inventory(self, force_refresh=False) -> List[dict]:
        def _fetch():
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    'SELECT * FROM inventory ORDER BY "Category", "Item ID"'
                ).fetchall()
                return [dict(r) for r in rows]
        return self._cached("inventory", _fetch, force_refresh)

    def add_inventory_item(self, item: dict):
        try:
            cols = INVENTORY_HEADERS
            placeholders = ", ".join("?" * len(cols))
            col_names = ", ".join(f'"{c}"' for c in cols)
            values = [str(item.get(c, "")) for c in cols]
            with self._lock, self._connect() as conn:
                conn.execute(
                    f'INSERT OR REPLACE INTO inventory ({col_names}) VALUES ({placeholders})',
                    values,
                )
            self.clear_cache()
        except sqlite3.Error as e:
            raise DatabaseError(f"Error adding inventory item: {e}")

    # ── Settings ──────────────────────────────────────────────────────────────

    SETTING_DEFAULTS = {
        "app_pin":               "1234",
        "business_name":         "Pucon Kayak Retreat",
        "currency":              "USD",
        "default_hourly_rate":   "",
        "default_half_day_rate": "",
        "default_full_day_rate": "",
        "default_multi_day_rate":"",
    }

    def get_settings(self) -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = dict(self.SETTING_DEFAULTS)
        for row in rows:
            result[row["key"]] = row["value"]
        return result

    def update_settings(self, updates: dict):
        with self._lock, self._connect() as conn:
            for key, value in updates.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(value)),
                )

    def delete_inventory_item(self, item_id: str) -> bool:
        try:
            with self._lock, self._connect() as conn:
                cur = conn.execute('DELETE FROM inventory WHERE "Item ID" = ?', (item_id,))
                if cur.rowcount == 0:
                    raise DatabaseError(f"Item '{item_id}' not found in inventory.")
            self.clear_cache()
            return True
        except DatabaseError:
            raise
        except sqlite3.Error as e:
            raise DatabaseError(f"Error deleting inventory item: {e}")

    def update_inventory_item(self, item_id: str, updates: dict) -> bool:
        try:
            set_parts = ", ".join(f'"{k}" = ?' for k in updates)
            values = list(updates.values()) + [item_id]
            with self._lock, self._connect() as conn:
                cur = conn.execute(
                    f'UPDATE inventory SET {set_parts} WHERE "Item ID" = ?', values
                )
                if cur.rowcount == 0:
                    raise DatabaseError(f"Item '{item_id}' not found in inventory.")
            self.clear_cache()
            return True
        except DatabaseError:
            raise
        except sqlite3.Error as e:
            raise DatabaseError(f"Error updating inventory item: {e}")

    # ── Reservations ──────────────────────────────────────────────────────────

    def get_reservations(self, force_refresh=False) -> List[dict]:
        def _fetch():
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    'SELECT * FROM reservations ORDER BY "Start Date & Time" DESC'
                ).fetchall()
                return [dict(r) for r in rows]
        return self._cached("reservations", _fetch, force_refresh)

    def add_reservation(self, reservation: dict):
        try:
            cols = RESERVATION_HEADERS
            placeholders = ", ".join("?" * len(cols))
            col_names = ", ".join(f'"{c}"' for c in cols)
            values = [str(reservation.get(c, "")) for c in cols]
            with self._lock, self._connect() as conn:
                conn.execute(
                    f'INSERT OR REPLACE INTO reservations ({col_names}) VALUES ({placeholders})',
                    values,
                )
            self.clear_cache()
        except sqlite3.Error as e:
            raise DatabaseError(f"Error adding reservation: {e}")

    def update_reservation(self, res_id: str, updates: dict) -> bool:
        try:
            set_parts = ", ".join(f'"{k}" = ?' for k in updates)
            values = list(updates.values()) + [res_id]
            with self._lock, self._connect() as conn:
                cur = conn.execute(
                    f'UPDATE reservations SET {set_parts} WHERE "Reservation ID" = ?', values
                )
                if cur.rowcount == 0:
                    raise DatabaseError(f"Reservation '{res_id}' not found.")
            self.clear_cache()
            return True
        except DatabaseError:
            raise
        except sqlite3.Error as e:
            raise DatabaseError(f"Error updating reservation: {e}")

    def check_conflicts(self, item_ids, start_str, end_str, exclude_id=None) -> List[dict]:
        try:
            reservations = self.get_reservations()
            new_start = _parse_dt(start_str)
            new_end = _parse_dt(end_str)
            if not new_start or not new_end:
                return []
            conflicts = []
            for r in reservations:
                if r.get("Reservation Status") in ("Canceled", "Returned"):
                    continue
                if exclude_id and str(r.get("Reservation ID")) == str(exclude_id):
                    continue
                ex_start = _parse_dt(r.get("Start Date & Time", ""))
                ex_end = _parse_dt(r.get("End Date & Time", ""))
                if not ex_start or not ex_end:
                    continue
                if new_start < ex_end and new_end > ex_start:
                    existing_items = {
                        i.strip()
                        for i in str(r.get("Item IDs", "")).split(",")
                        if i.strip()
                    }
                    if existing_items.intersection(set(item_ids)):
                        conflicts.append(r)
            return conflicts
        except DatabaseError:
            raise
        except Exception as e:
            raise DatabaseError(f"Error checking conflicts: {e}")


def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
