import time
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Our canonical inventory columns — written to sheet when setting up or adding equipment
INVENTORY_HEADERS = [
    "Item ID", "Category", "Name/Description", "Size", "Status", "Condition Notes",
    "Hourly Rate", "Half-Day Rate", "Full-Day Rate", "Multi-Day Rate",
]

# Columns from the pre-existing PKR rental sheet
# Combined Label → Item ID, Brand → Category, Full Model → Name/Description
_LEGACY_COL_MAP = {
    "Combined Label":       "Item ID",
    "Brand":                "Category",
    "Full Model":           "Name/Description",
    "Kayak Name (Short)":   "_short_name",  # kept as metadata
}

RESERVATION_HEADERS = [
    "Reservation ID", "Customer Name", "Customer Phone", "Customer Email",
    "Item IDs", "Start Date & Time", "End Date & Time", "Rental Type",
    "Payment Status", "Payment Amount", "Waiver Signed", "Reservation Status",
    "Notes", "Created At",
]


class SheetsError(Exception):
    pass


class SheetsClient:
    def __init__(self, credentials_file, sheet_id):
        self._credentials_file = credentials_file
        self._sheet_id = sheet_id
        self._client = None
        self._spreadsheet = None
        self._cache = {}
        self._cache_ts = {}
        self._cache_ttl = 30  # seconds

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self):
        try:
            if self._client is None:
                creds = Credentials.from_service_account_file(
                    self._credentials_file, scopes=SCOPES
                )
                self._client = gspread.authorize(creds)
            if self._spreadsheet is None:
                if not self._sheet_id:
                    raise SheetsError(
                        "GOOGLE_SHEET_ID is not set. Add it to your .env file."
                    )
                self._spreadsheet = self._client.open_by_key(self._sheet_id)
            return self._spreadsheet
        except FileNotFoundError:
            raise SheetsError(
                f'Credentials file "{self._credentials_file}" not found. '
                "Place your Google service account JSON file in the project folder "
                "and set GOOGLE_CREDENTIALS_FILE in .env."
            )
        except gspread.exceptions.APIError as e:
            raise SheetsError(f"Google Sheets API error: {e}")
        except SheetsError:
            raise
        except Exception as e:
            raise SheetsError(f"Cannot connect to Google Sheets: {e}")

    def _get_ws(self, name):
        ss = self._connect()
        try:
            return ss.worksheet(name)
        except gspread.WorksheetNotFound:
            raise SheetsError(
                f'Worksheet "{name}" not found. Your Google Sheet must have tabs '
                'named exactly "Inventory" and "Reservations".'
            )

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

    def reset_connection(self):
        self._client = None
        self._spreadsheet = None
        self.clear_cache()

    # ── Inventory ─────────────────────────────────────────────────────────────

    def _normalize_row(self, row: dict) -> dict:
        """Map legacy column names to canonical names so the app works with
        the existing PKR sheet (Brand / Full Model / Combined Label) as well
        as our own column layout."""
        # If the row already has our canonical 'Item ID', nothing to do
        if "Item ID" in row and row["Item ID"]:
            return row
        out = dict(row)
        for old, new in _LEGACY_COL_MAP.items():
            if old in row and new not in out:
                out[new] = row[old]
        # Default Status to Available when column is missing/empty
        if not out.get("Status"):
            out["Status"] = "Available"
        return out

    def get_inventory(self, force_refresh=False):
        raw = self._cached(
            "inventory",
            lambda: self._get_ws("Inventory").get_all_records(),
            force_refresh,
        )
        return [self._normalize_row(r) for r in raw]

    def add_inventory_item(self, item: dict):
        """Append a new equipment row. Ensures the sheet has all expected
        columns first — adds any that are missing as new header columns."""
        try:
            ws = self._get_ws("Inventory")
            headers = ws.row_values(1)

            # If the sheet is empty, write our headers
            if not headers:
                ws.append_row(INVENTORY_HEADERS)
                headers = list(INVENTORY_HEADERS)

            # Add any missing columns on the right
            missing = [h for h in INVENTORY_HEADERS if h not in headers]
            if missing:
                for col_name in missing:
                    col_idx = len(headers) + 1
                    ws.update_cell(1, col_idx, col_name)
                    headers.append(col_name)

            row = [str(item.get(h, "")) for h in headers]
            ws.append_row(row)
            self.clear_cache()
        except SheetsError:
            raise
        except Exception as e:
            raise SheetsError(f"Error adding inventory item: {e}")

    def update_inventory_item(self, item_id, updates):
        try:
            ws = self._get_ws("Inventory")
            records = ws.get_all_records()
            headers = ws.row_values(1)

            for idx, row in enumerate(records):
                normalized = self._normalize_row(row)
                if str(normalized.get("Item ID")) == str(item_id):
                    row_num = idx + 2
                    for col_name, value in updates.items():
                        if col_name in headers:
                            col_idx = headers.index(col_name) + 1
                            ws.update_cell(row_num, col_idx, value)
                        else:
                            # Column missing — add it
                            new_col_idx = len(headers) + 1
                            ws.update_cell(1, new_col_idx, col_name)
                            ws.update_cell(row_num, new_col_idx, value)
                            headers.append(col_name)
                    self.clear_cache()
                    return True

            raise SheetsError(f"Item '{item_id}' not found in Inventory.")
        except SheetsError:
            raise
        except Exception as e:
            raise SheetsError(f"Error updating inventory item: {e}")

    # ── Reservations ──────────────────────────────────────────────────────────

    def get_reservations(self, force_refresh=False):
        return self._cached(
            "reservations",
            lambda: self._get_ws("Reservations").get_all_records(),
            force_refresh,
        )

    def add_reservation(self, reservation):
        try:
            ws = self._get_ws("Reservations")
            headers = ws.row_values(1)
            if not headers:
                ws.append_row(RESERVATION_HEADERS)
                headers = list(RESERVATION_HEADERS)
            row = [str(reservation.get(h, "")) for h in headers]
            ws.append_row(row)
            self.clear_cache()
        except SheetsError:
            raise
        except Exception as e:
            raise SheetsError(f"Error adding reservation: {e}")

    def update_reservation(self, res_id, updates):
        try:
            ws = self._get_ws("Reservations")
            records = ws.get_all_records()
            headers = ws.row_values(1)
            for idx, row in enumerate(records):
                if str(row.get("Reservation ID")) == str(res_id):
                    row_num = idx + 2
                    for col_name, value in updates.items():
                        if col_name in headers:
                            col_idx = headers.index(col_name) + 1
                            ws.update_cell(row_num, col_idx, str(value))
                    self.clear_cache()
                    return True
            raise SheetsError(f"Reservation {res_id} not found.")
        except SheetsError:
            raise
        except Exception as e:
            raise SheetsError(f"Error updating reservation: {e}")

    def check_conflicts(self, item_ids, start_str, end_str, exclude_id=None):
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
        except SheetsError:
            raise
        except Exception as e:
            raise SheetsError(f"Error checking conflicts: {e}")


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
