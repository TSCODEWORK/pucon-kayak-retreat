"""
SheetsSyncer — pushes SQLite data to Google Sheets in a background thread.
Google Sheets is a read-only mirror; all writes go to SQLite first.
"""

import threading
import logging
from sheets import SheetsClient, SheetsError, INVENTORY_HEADERS, RESERVATION_HEADERS

log = logging.getLogger(__name__)


class SheetsSyncer:
    def __init__(self, sheets_client: SheetsClient):
        self._sheets = sheets_client
        self._lock = threading.Lock()
        self._active = False

    def push_async(self, db_client) -> None:
        """Fire-and-forget: sync SQLite → Sheets in a background thread."""
        with self._lock:
            if self._active:
                return  # already syncing, skip — next write will trigger another
            self._active = True

        def _run():
            try:
                self._push(db_client)
            except Exception as e:
                log.warning("Sheets sync failed (non-fatal): %s", e)
            finally:
                with self._lock:
                    self._active = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def push_now(self, db_client) -> dict:
        """Synchronous push — called from the manual /api/sync endpoint."""
        try:
            stats = self._push(db_client)
            return {"status": "ok", "stats": stats}
        except SheetsError as e:
            return {"status": "error", "message": str(e)}

    def _push(self, db_client) -> dict:
        inv = db_client.get_inventory(force_refresh=True)
        res = db_client.get_reservations(force_refresh=True)
        self._sync_tab("Inventory", INVENTORY_HEADERS, "Item ID", inv)
        self._sync_tab("Reservations", RESERVATION_HEADERS, "Reservation ID", res)
        return {"inventory_rows": len(inv), "reservation_rows": len(res)}

    def _sync_tab(self, tab_name: str, headers: list, pk: str, rows: list[dict]):
        try:
            ws = self._sheets._get_ws(tab_name)
        except SheetsError:
            # Tab doesn't exist — create it
            ss = self._sheets._connect()
            ws = ss.add_worksheet(tab_name, rows=max(500, len(rows) + 10), cols=len(headers) + 2)

        # Ensure header row
        current_headers = ws.row_values(1)
        if not current_headers:
            ws.append_row(headers)
            current_headers = list(headers)

        # Add any missing columns
        for col in headers:
            if col not in current_headers:
                ws.update_cell(1, len(current_headers) + 1, col)
                current_headers.append(col)

        if not rows:
            return

        # Build id → sheet row index map
        all_values = ws.get_all_values()
        header_row = all_values[0] if all_values else []
        pk_col_idx = header_row.index(pk) if pk in header_row else 0

        sheet_ids: dict[str, int] = {}  # id → 1-based row number
        for i, row_vals in enumerate(all_values[1:], start=2):
            if row_vals and len(row_vals) > pk_col_idx:
                sheet_ids[row_vals[pk_col_idx]] = i

        new_rows = []
        for record in rows:
            record_id = str(record.get(pk, ""))
            # Use canonical headers (not the sheet's current header_row) so data
            # always aligns with our schema even if the sheet has extra legacy columns.
            row_data = [str(record.get(h, "")) for h in headers]

            if record_id in sheet_ids:
                # Update in place
                row_num = sheet_ids[record_id]
                ws.update(f"A{row_num}", [row_data])
            else:
                new_rows.append(row_data)

        if new_rows:
            ws.append_rows(new_rows)

    def pull_from_sheets(self, db_client) -> dict:
        """Pull Sheet data into SQLite (INSERT OR REPLACE — safe to repeat)."""
        inv_pulled = 0
        res_pulled = 0
        try:
            # Pull inventory — skip rows with empty/whitespace Item ID (#4)
            try:
                ws = self._sheets._get_ws("Inventory")
                rows = ws.get_all_records()
                for row in rows:
                    item_id = str(row.get("Item ID", "")).strip()
                    if not item_id:
                        log.debug("Skipping inventory row with empty Item ID during pull")
                        continue
                    # Skip rows that are suspiciously empty (only ID populated)
                    non_empty = sum(1 for v in row.values() if str(v).strip())
                    if non_empty < 2:
                        log.debug("Skipping near-empty inventory row: %s", item_id)
                        continue
                    db_client.add_inventory_item(row)
                    inv_pulled += 1
            except SheetsError:
                pass  # Tab may not exist yet

            # Pull reservations — same guards (#4)
            try:
                ws = self._sheets._get_ws("Reservations")
                rows = ws.get_all_records()
                for row in rows:
                    res_id = str(row.get("Reservation ID", "")).strip()
                    if not res_id:
                        log.debug("Skipping reservation row with empty Reservation ID during pull")
                        continue
                    non_empty = sum(1 for v in row.values() if str(v).strip())
                    if non_empty < 3:
                        log.debug("Skipping near-empty reservation row: %s", res_id)
                        continue
                    db_client.add_reservation(row)
                    res_pulled += 1
            except SheetsError:
                pass

            db_client.clear_cache()
        except Exception as e:
            raise SheetsError(str(e))
        return {"inventory_pulled": inv_pulled, "reservations_pulled": res_pulled}
