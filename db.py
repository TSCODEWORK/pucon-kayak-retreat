"""
DatabaseClient — SQLite primary store for Pucon Kayak Retreat.

Column names intentionally match the Google Sheets headers so app.py
needs no changes when swapping SheetsClient for DatabaseClient.
"""

import os
import logging
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Optional, List

log = logging.getLogger(__name__)

from utils import _parse_dt, Cache  # single source of truth for both helpers

# Canonical column names (match Sheets headers exactly)
INVENTORY_HEADERS = [
    "Item ID", "Category", "Name/Description", "Size", "Status",
    "Condition Notes", "Hourly Rate", "Half-Day Rate", "Full-Day Rate",
    "Multi-Day Rate", "Quantity",
]
RESERVATION_HEADERS = [
    "Reservation ID", "Customer Name", "Customer Phone", "Customer Email",
    "Item IDs", "Start Date & Time", "End Date & Time", "Rental Type",
    "Payment Status", "Payment Amount", "Waiver Signed", "Reservation Status",
    "Notes", "Created At", "Original Payment Amount", "Discount Percent",
    "Waiver File",
]


class DatabaseError(Exception):
    pass


class DatabaseClient:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("PKR_DB_PATH", ".")
        self._db_file = str(Path(db_path) / "rental.db")
        self._lock = threading.Lock()
        self._cache = Cache(ttl=5)  # short TTL — SQLite is local, reads are fast
        self._init_db()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:  # type: ignore[return]
        conn = sqlite3.connect(self._db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── Schema version ────────────────────────────────────────────────────────
    # Bump this number every time you add columns/tables.
    # Each migration step is idempotent (safe to run on an already-upgraded DB).
    SCHEMA_VERSION = 3

    # Each entry is (target_version, sql_or_callable).
    # SQL strings are executed directly; callables receive the connection.
    _MIGRATIONS = [
        # v1 → v2: add Quantity column to inventory
        (2, 'ALTER TABLE inventory ADD COLUMN "Quantity" INTEGER DEFAULT 1'),
        # v2 → v3: discount breakdown + waiver file path on reservations
        (3, 'ALTER TABLE reservations ADD COLUMN "Original Payment Amount" TEXT DEFAULT \'\''),
        (3, 'ALTER TABLE reservations ADD COLUMN "Discount Percent" TEXT DEFAULT \'\''),
        (3, 'ALTER TABLE reservations ADD COLUMN "Waiver File" TEXT DEFAULT \'\''),
    ]

    def _init_db(self):
        with self._lock, self._connect() as conn:
            # Create base tables (IF NOT EXISTS — safe on any version)
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
                    "Multi-Day Rate"   TEXT DEFAULT '',
                    "Quantity"         INTEGER DEFAULT 1
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
                    "Created At"        TEXT DEFAULT '',
                    "Original Payment Amount" TEXT DEFAULT '',
                    "Discount Percent"  TEXT DEFAULT '',
                    "Waiver File"       TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );
            """)
            self._run_migrations(conn)

    def _run_migrations(self, conn):
        """Apply any pending schema migrations in order, then record the version."""
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current = row["version"] if row else 0

        for target_version, migration in self._MIGRATIONS:
            if current >= target_version:
                continue
            try:
                if callable(migration):
                    migration(conn)
                else:
                    conn.execute(migration)
                log.info("DB migration applied: v%d", target_version)
            except sqlite3.OperationalError as e:
                # Column/table already exists — migration was already applied
                log.debug("Migration v%d skipped (already applied): %s", target_version, e)

        # Record current schema version
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (self.SCHEMA_VERSION,),
        )

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def clear_cache(self):
        self._cache.clear()

    # ── Inventory ─────────────────────────────────────────────────────────────

    def get_inventory(self, force_refresh=False) -> List[dict]:
        def _fetch():
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    'SELECT * FROM inventory ORDER BY "Category", "Item ID"'
                ).fetchall()
                return [dict(r) for r in rows]
        return self._cache.get("inventory", _fetch, force_refresh)

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
        "rate_currency":         "CLP",   # currency in which item rates are stored
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
        return self._cache.get("reservations", _fetch, force_refresh)

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

    def update_reservation_conditional(self, res_id: str, expected_status: str, updates: dict) -> bool:
        """Update a reservation only if its current status matches expected_status.

        Returns True if the row was updated, False if the status had already changed
        (a concurrent request won the race — e.g. double-click on Check Out).
        This is an atomic DB-level guard against double-submission races (#6).
        """
        try:
            set_parts = ", ".join(f'"{k}" = ?' for k in updates)
            values = list(updates.values()) + [res_id, expected_status]
            with self._lock, self._connect() as conn:
                cur = conn.execute(
                    f'UPDATE reservations SET {set_parts} '
                    f'WHERE "Reservation ID" = ? AND "Reservation Status" = ?',
                    values,
                )
                updated = cur.rowcount > 0
            if updated:
                self.clear_cache()
            return updated
        except sqlite3.Error as e:
            raise DatabaseError(f"Error updating reservation: {e}")

    def check_conflicts(self, item_ids, start_str, end_str, exclude_id=None, force_refresh=False) -> List[dict]:
        """Quantity-aware conflict check.

        `item_ids` may contain repeated entries to request more than one unit
        of the same Item ID (e.g. ["Helmet", "Helmet"] = 2 helmets). A conflict
        is only reported for an item once the number of units already reserved
        by *other* overlapping active reservations plus the number requested
        here exceeds that item's inventory Quantity — not on every overlap.
        """
        try:
            reservations = self.get_reservations(force_refresh=force_refresh)
            inventory = self.get_inventory(force_refresh=force_refresh)
            qty_map = {}
            for i in inventory:
                try:
                    qty_map[str(i.get("Item ID"))] = int(i.get("Quantity") or 1)
                except (TypeError, ValueError):
                    qty_map[str(i.get("Item ID"))] = 1

            new_start = _parse_dt(start_str)
            new_end = _parse_dt(end_str)
            if not new_start or not new_end:
                return []

            requested_counts = Counter(str(i).strip() for i in item_ids if str(i).strip())

            # For each requested item, collect the overlapping reservations that
            # also use it, along with how many units each one holds.
            overlapping_by_item = {}
            for r in reservations:
                if r.get("Reservation Status") in ("Canceled", "Returned"):
                    continue
                if exclude_id and str(r.get("Reservation ID")) == str(exclude_id):
                    continue
                ex_start = _parse_dt(r.get("Start Date & Time", ""))
                ex_end = _parse_dt(r.get("End Date & Time", ""))
                if not ex_start or not ex_end:
                    continue
                if not (new_start < ex_end and new_end > ex_start):
                    continue
                r_counts = Counter(
                    i.strip() for i in str(r.get("Item IDs", "")).split(",") if i.strip()
                )
                for iid, cnt in r_counts.items():
                    if iid in requested_counts:
                        overlapping_by_item.setdefault(iid, []).append((r, cnt))

            conflicts = []
            seen_res_ids = set()
            for iid, want in requested_counts.items():
                total_qty = qty_map.get(iid, 1)
                already_booked = sum(cnt for _, cnt in overlapping_by_item.get(iid, []))
                if already_booked + want > total_qty:
                    blamed = overlapping_by_item.get(iid, [])
                    for r, _ in blamed:
                        rid = r.get("Reservation ID")
                        if rid not in seen_res_ids:
                            seen_res_ids.add(rid)
                            conflicts.append(r)
                    # No specific reservation to blame — the request alone exceeds
                    # how many units exist (e.g. asking for 2 of a quantity-1 item
                    # with nothing else booked). Still surface it as a conflict.
                    if not blamed:
                        conflicts.append({
                            "Reservation ID": "",
                            "Customer Name": f"insufficient inventory — only {total_qty} '{iid}' in stock",
                            "Start Date & Time": "", "End Date & Time": "",
                        })
            return conflicts
        except DatabaseError:
            raise
        except Exception as e:
            raise DatabaseError(f"Error checking conflicts: {e}")


# _parse_dt is re-exported here so existing callers using `from db import _parse_dt`
# continue to work without changes.
__all__ = ["DatabaseClient", "DatabaseError", "_parse_dt"]
