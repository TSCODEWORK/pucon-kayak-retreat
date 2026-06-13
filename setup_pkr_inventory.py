"""
Populate the SQLite database with the full official PKR kayak fleet.
Source: PKR guest registration form (updated April 30, 2026)

Usage:
  python setup_pkr_inventory.py [--db PATH]

Options:
  --clear   Wipe existing inventory before writing (default: skip duplicates)
  --db PATH Directory for rental.db (default: current directory)
"""

import os
import sys
import sqlite3
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

HEADERS = [
    "Item ID", "Category", "Name/Description", "Size", "Status", "Condition Notes",
    "Hourly Rate", "Half-Day Rate", "Full-Day Rate", "Multi-Day Rate",
]

# ── Sizing guide (per PKR registration, April 30 2026) ───────────────────────
# S: under 70 kg / 155 lbs
# M: 70–90 kg / 155–200 lbs
# L: 90+ kg / 200+ lbs

SIZING_NOTE = {
    "S": "S — under 70 kg / 155 lbs",
    "M": "M — 70–90 kg / 155–200 lbs",
    "L": "L — 90+ kg / 200+ lbs",
    "":  "",
}

# fmt: (Category, Size, Model)  →  Item ID = "Category - Model"
FLEET = [
    # ── DAGGER ────────────────────────────────────────────────────────────────
    ("Dagger",        "S", "Code S"),
    ("Dagger",        "S", "Mamba 7.6"),
    ("Dagger",        "M", "Code M"),
    ("Dagger",        "M", "Indra S/M"),
    ("Dagger",        "M", "Phantom M"),
    ("Dagger",        "L", "Code L"),
    ("Dagger",        "L", "Indra M/L"),

    # ── JACKSON ───────────────────────────────────────────────────────────────
    ("Jackson",       "S", "Rockstar S"),
    ("Jackson",       "S", "Gnarvana S"),
    ("Jackson",       "S", "Lil Hero"),
    ("Jackson",       "M", "Antix 2.0 M"),
    ("Jackson",       "M", "Antix 3.0 M"),
    ("Jackson",       "M", "Gnarvana M"),
    ("Jackson",       "M", "Nirvana M"),
    ("Jackson",       "M", "Rockstar 4.0"),
    ("Jackson",       "L", "Antix 2.0 L"),
    ("Jackson",       "L", "Flow L"),
    ("Jackson",       "L", "Gnarvana L"),
    ("Jackson",       "L", "Nirvana L"),
    ("Jackson",       "L", "Karma XL"),

    # ── LIQUID LOGIC ──────────────────────────────────────────────────────────
    ("Liquid Logic",  "M", "Party Braaap 69"),

    # ── PYRANHA ───────────────────────────────────────────────────────────────
    ("Pyranha",       "S", "Ripper"),
    ("Pyranha",       "S", "ReactR S"),
    ("Pyranha",       "M", "ReactR M"),
    ("Pyranha",       "M", "Scorch M"),
    ("Pyranha",       "L", "ReactR L"),
    ("Pyranha",       "L", "Scorch L"),

    # ── WAKA ──────────────────────────────────────────────────────────────────
    ("Waka",          "S", "Goat"),
    ("Waka",          "S", "Billy Goat"),
    ("Waka",          "M", "Stoke"),
    ("Waka",          "M", "Steeze"),
    ("Waka",          "M", "OG M/L"),
    ("Waka",          "M", "Gangsta M/L"),
    ("Waka",          "L", "Puffy Steeze"),

    # ── SPADE ─────────────────────────────────────────────────────────────────
    ("Spade",         "S", "La Queen"),

    # ── SPECIAL ───────────────────────────────────────────────────────────────
    ("Inflatable",    "",  "Inflatable Kayak"),
]


def make_row(category, size, model):
    item_id = f"{category} - {model}"
    description = f"{category} {model}"
    condition = SIZING_NOTE.get(size, "")
    return {
        "Item ID":          item_id,
        "Category":         category,
        "Name/Description": description,
        "Size":             size,
        "Status":           "Available",
        "Condition Notes":  condition,
        "Hourly Rate":      "",
        "Half-Day Rate":    "",
        "Full-Day Rate":    "",
        "Multi-Day Rate":   "",
    }


def main():
    from db import DatabaseClient

    print(f"Setting up SQLite database at: {Path(db_path) / 'rental.db'}")
    db = DatabaseClient(db_path)

    if clear:
        db_file = str(Path(db_path) / "rental.db")
        conn = sqlite3.connect(db_file)
        conn.execute("DELETE FROM inventory")
        conn.commit()
        conn.close()
        print("  Cleared existing inventory.")
        existing_ids: set = set()
    else:
        existing = db.get_inventory()
        existing_ids = {str(r.get("Item ID", "")) for r in existing}
        existing_ids.discard("")
        print(f"  {len(existing_ids)} existing item(s) found — skipping duplicates.")

    added = 0
    skipped = 0
    for category, size, model in FLEET:
        row_data = make_row(category, size, model)
        item_id = row_data["Item ID"]
        if item_id in existing_ids:
            skipped += 1
            continue
        db.add_inventory_item(row_data)
        added += 1

    print(f"  ✓ Added {added} kayak(s). Skipped {skipped} already-existing item(s).")
    print(f"\n✓ Done! {len(FLEET)} models in the official PKR fleet.")
    print("  Open the app → Inventory to see all kayaks.")


if __name__ == "__main__":
    main()
