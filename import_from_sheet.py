"""
One-time migration: Google Sheets → SQLite

Usage:
  python import_from_sheet.py [--db PATH]

Options:
  --db PATH   Directory for rental.db (default: current directory)
  --clear     Drop existing SQLite data before importing

Requires .env with GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_FILE set.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

db_path = "."
clear = False
for i, arg in enumerate(sys.argv[1:]):
    if arg == "--db" and i + 2 <= len(sys.argv[1:]):
        db_path = sys.argv[i + 2]
    elif arg == "--clear":
        clear = True

os.environ["PKR_DB_PATH"] = db_path

from sheets import SheetsClient, SheetsError
from db import DatabaseClient, INVENTORY_HEADERS, RESERVATION_HEADERS
import sqlite3

sheets = SheetsClient(
    credentials_file=os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
    sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""),
)
db = DatabaseClient(db_path)

if clear:
    db_file = str(Path(db_path) / "rental.db")
    conn = sqlite3.connect(db_file)
    conn.execute("DELETE FROM inventory")
    conn.execute("DELETE FROM reservations")
    conn.commit()
    conn.close()
    print("Cleared existing SQLite data.")

print("Fetching inventory from Google Sheets…")
try:
    inventory = sheets.get_inventory()
    print(f"  {len(inventory)} item(s) found.")
    for item in inventory:
        db.add_inventory_item(item)
    print(f"  ✓ Imported {len(inventory)} inventory item(s).")
except SheetsError as e:
    print(f"  ERROR: {e}")

print("\nFetching reservations from Google Sheets…")
try:
    reservations = sheets.get_reservations()
    print(f"  {len(reservations)} reservation(s) found.")
    for res in reservations:
        db.add_reservation(res)
    print(f"  ✓ Imported {len(reservations)} reservation(s).")
except SheetsError as e:
    print(f"  ERROR: {e}")

print(f"\nDone. SQLite DB at: {Path(db_path) / 'rental.db'}")
