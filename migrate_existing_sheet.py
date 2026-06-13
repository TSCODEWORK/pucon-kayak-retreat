"""
Migrate the existing PKR Google Sheet to work with the rental tracker app.

The existing Inventory sheet has columns:
  Brand | Full Model | Kayak Name (Short) | Combined Label

This script adds the missing columns our app needs (Status, Condition Notes,
Hourly Rate, Half-Day Rate, Full-Day Rate, Multi-Day Rate) and creates a
blank Reservations tab if one doesn't already exist.

Existing data is NOT modified — only new columns are added.

Usage:
  python migrate_existing_sheet.py
"""

import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Columns the app needs that the legacy sheet doesn't have
NEW_INVENTORY_COLS = [
    "Item ID",         # mirrors Combined Label — app uses this as the unique key
    "Status",          # Available | Rented | Maintenance
    "Condition Notes",
    "Hourly Rate",
    "Half-Day Rate",
    "Full-Day Rate",
    "Multi-Day Rate",
]

RESERVATION_HEADERS = [
    "Reservation ID", "Customer Name", "Customer Phone", "Customer Email",
    "Item IDs", "Start Date & Time", "End Date & Time", "Rental Type",
    "Payment Status", "Payment Amount", "Waiver Signed", "Reservation Status",
    "Notes", "Created At",
]


def main():
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    sheet_id   = os.environ.get("GOOGLE_SHEET_ID", "")

    if not sheet_id:
        print("ERROR: GOOGLE_SHEET_ID not set in .env")
        return

    print(f"Connecting to Google Sheets ({sheet_id[:8]}…)")
    creds  = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(creds)
    ss     = client.open_by_key(sheet_id)

    # ── Inventory tab ──────────────────────────────────────────────────────────
    print("\nChecking Inventory tab…")
    try:
        inv_ws = ss.worksheet("Inventory")
    except gspread.WorksheetNotFound:
        print("  No 'Inventory' tab found — please rename your equipment tab to 'Inventory'")
        return

    headers = inv_ws.row_values(1)
    print(f"  Existing columns: {headers}")

    # Add missing columns
    added = []
    for col_name in NEW_INVENTORY_COLS:
        if col_name not in headers:
            col_idx = len(headers) + 1
            inv_ws.update_cell(1, col_idx, col_name)
            headers.append(col_name)
            added.append(col_name)

    if added:
        print(f"  ✓ Added columns: {added}")
    else:
        print("  ✓ All columns already present.")

    # Populate 'Item ID' from 'Combined Label' where Item ID is blank
    combined_label_col = headers.index("Combined Label") + 1 if "Combined Label" in headers else None
    item_id_col        = headers.index("Item ID") + 1        if "Item ID"        in headers else None

    if combined_label_col and item_id_col:
        all_rows = inv_ws.get_all_values()
        updated_ids = 0
        for row_idx, row in enumerate(all_rows[1:], start=2):  # skip header
            existing_id = row[item_id_col - 1] if len(row) >= item_id_col else ""
            label       = row[combined_label_col - 1] if len(row) >= combined_label_col else ""
            if not existing_id and label:
                inv_ws.update_cell(row_idx, item_id_col, label)
                updated_ids += 1
        if updated_ids:
            print(f"  ✓ Populated 'Item ID' for {updated_ids} rows from 'Combined Label'.")

    # Populate 'Status' = 'Available' where blank
    status_col = headers.index("Status") + 1 if "Status" in headers else None
    if status_col:
        all_rows = inv_ws.get_all_values()
        updated_status = 0
        for row_idx, row in enumerate(all_rows[1:], start=2):
            existing = row[status_col - 1] if len(row) >= status_col else ""
            if not existing:
                inv_ws.update_cell(row_idx, status_col, "Available")
                updated_status += 1
        if updated_status:
            print(f"  ✓ Set Status = 'Available' for {updated_status} rows.")

    # ── Reservations tab ───────────────────────────────────────────────────────
    print("\nChecking Reservations tab…")
    try:
        res_ws = ss.worksheet("Reservations")
        print("  ✓ Already exists.")
    except gspread.WorksheetNotFound:
        res_ws = ss.add_worksheet("Reservations", rows=500, cols=20)
        res_ws.append_row(RESERVATION_HEADERS)
        print("  ✓ Created with headers.")

    print("\n✓ Migration complete! Your app is now ready to use with this sheet.")
    print("  Restart the app to pick up the changes.")


if __name__ == "__main__":
    main()
