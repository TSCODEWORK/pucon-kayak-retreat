"""
Populate your Google Sheet with sample inventory and reservations for testing.

Usage:
  python setup_sample_data.py

Requires .env to be configured with GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_FILE.
WARNING: This will APPEND rows to your sheet. Clear existing data first if needed.
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

INVENTORY = [
    # Item ID, Category, Name/Description, Status, Condition Notes, Hourly, Half-Day, Full-Day, Multi-Day
    ["SKAYAK-01", "Single Kayak", "Single Kayak #1 — Red",       "Available", "Good condition", 15, 45, 75, 60],
    ["SKAYAK-02", "Single Kayak", "Single Kayak #2 — Blue",      "Available", "Minor scratch on hull", 15, 45, 75, 60],
    ["SKAYAK-03", "Single Kayak", "Single Kayak #3 — Yellow",    "Available", "Good condition", 15, 45, 75, 60],
    ["TKAYAK-01", "Tandem Kayak", "Tandem Kayak #1 — Green",     "Available", "Good condition", 25, 70, 110, 90],
    ["TKAYAK-02", "Tandem Kayak", "Tandem Kayak #2 — Orange",    "Maintenance", "Seat buckle needs repair", 25, 70, 110, 90],
    ["PADDLE-01", "Paddle",       "Paddle #1 — 220cm Fiberglass","Available", "", 5, 12, 20, 15],
    ["PADDLE-02", "Paddle",       "Paddle #2 — 220cm Fiberglass","Available", "", 5, 12, 20, 15],
    ["PADDLE-03", "Paddle",       "Paddle #3 — 230cm Aluminum",  "Available", "Slight bend, functional", 5, 12, 20, 15],
    ["PADDLE-04", "Paddle",       "Paddle #4 — 230cm Aluminum",  "Available", "", 5, 12, 20, 15],
    ["PFD-01",    "PFD",          "PFD #1 — Size M",             "Available", "Good condition", 5, 10, 15, 12],
    ["PFD-02",    "PFD",          "PFD #2 — Size L",             "Available", "Good condition", 5, 10, 15, 12],
    ["PFD-03",    "PFD",          "PFD #3 — Size S",             "Available", "Good condition", 5, 10, 15, 12],
    ["PFD-04",    "PFD",          "PFD #4 — Size XL",            "Available", "Strap fraying slightly", 5, 10, 15, 12],
    ["HELMET-01", "Helmet",       "Helmet #1 — M/L",             "Available", "Good condition", 5, 10, 15, 12],
    ["HELMET-02", "Helmet",       "Helmet #2 — S/M",             "Available", "Good condition", 5, 10, 15, 12],
    ["DRYBAG-01", "Dry Bag",      "Dry Bag #1 — 10L",            "Available", "", 3, 8, 12, 10],
    ["DRYBAG-02", "Dry Bag",      "Dry Bag #2 — 20L",            "Available", "", 4, 10, 15, 12],
]

def sample_reservations():
    now = datetime.now()
    today = now.replace(hour=9, minute=0, second=0, microsecond=0)

    def dt(offset_days, hour, minute=0):
        return (today + timedelta(days=offset_days)).replace(hour=hour, minute=minute).strftime("%Y-%m-%dT%H:%M")

    return [
        # Reservation ID, Customer Name, Phone, Email, Item IDs, Start, End, Type, Payment Status, Amount, Waiver, Status, Notes, Created
        ["RES-SAMPLE1", "Maria González",   "+56 9 8765 4321", "maria@example.com",
         "SKAYAK-01, PADDLE-01, PFD-01",
         dt(0, 9), dt(0, 13), "Half-Day", "Paid in Full", "90", "Yes", "Upcoming",
         "Experienced paddler, solo trip", now.strftime("%Y-%m-%d %H:%M")],

        ["RES-SAMPLE2", "Carlos Pérez",     "+56 9 1234 5678", "carlos@example.com",
         "TKAYAK-01, PADDLE-02, PADDLE-03, PFD-02, PFD-03",
         dt(0, 14), dt(0, 18), "Half-Day", "Deposit Paid", "140", "Yes", "Upcoming",
         "Family — 2 adults in tandem", now.strftime("%Y-%m-%d %H:%M")],

        ["RES-SAMPLE3", "Sofia Müller",     "+56 9 5555 0000", "sofia@example.com",
         "SKAYAK-02, SKAYAK-03, PADDLE-04, PFD-04, HELMET-01, HELMET-02, DRYBAG-01",
         dt(-1, 9), dt(-1, 17), "Full-Day", "Paid in Full", "210", "Yes", "Returned",
         "Guided group tour", now.strftime("%Y-%m-%d %H:%M")],

        ["RES-SAMPLE4", "James Whitfield",  "+1 555 867 5309",  "james@example.com",
         "SKAYAK-01, PADDLE-01, PFD-01, DRYBAG-02",
         dt(-2, 10), dt(0, 17), "Multi-Day", "Paid in Full", "185", "Yes", "Checked Out",
         "Multi-day self-supported expedition", now.strftime("%Y-%m-%d %H:%M")],

        ["RES-SAMPLE5", "Ana Rodríguez",    "+56 9 9999 1111", "ana@example.com",
         "TKAYAK-01, PADDLE-02, PADDLE-03, PFD-02, PFD-03, HELMET-01",
         dt(2, 9), dt(2, 12), "Hourly", "Unpaid", "105", "No", "Upcoming",
         "Beginner lesson booked", now.strftime("%Y-%m-%d %H:%M")],

        ["RES-SAMPLE6", "Tomás Herrera",    "+56 9 4444 2222", "tomas@example.com",
         "SKAYAK-03, PADDLE-04, PFD-04",
         dt(3, 13), dt(4, 17), "Multi-Day", "Deposit Paid", "120", "Yes", "Upcoming",
         "", now.strftime("%Y-%m-%d %H:%M")],

        ["RES-SAMPLE7", "Luisa Fontaine",   "+56 9 3333 8888", "luisa@example.com",
         "SKAYAK-02, PADDLE-01, PFD-01",
         dt(-3, 9), dt(-3, 13), "Half-Day", "Paid in Full", "75", "Yes", "Canceled",
         "Canceled due to weather", now.strftime("%Y-%m-%d %H:%M")],
    ]


def main():
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    sheet_id   = os.environ.get("GOOGLE_SHEET_ID", "")

    if not sheet_id:
        print("ERROR: GOOGLE_SHEET_ID not set in .env")
        return

    print(f"Connecting to Google Sheets (sheet ID: {sheet_id[:8]}…)")
    creds  = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(creds)
    ss     = client.open_by_key(sheet_id)

    # ── Inventory ──────────────────────────────────────────────────────────────
    print("Setting up Inventory tab…")
    try:
        inv_ws = ss.worksheet("Inventory")
    except gspread.WorksheetNotFound:
        inv_ws = ss.add_worksheet("Inventory", rows=100, cols=20)

    inv_ws.clear()
    headers = ["Item ID", "Category", "Name/Description", "Status", "Condition Notes",
               "Hourly Rate", "Half-Day Rate", "Full-Day Rate", "Multi-Day Rate"]
    inv_ws.append_row(headers)
    for row in INVENTORY:
        inv_ws.append_row([str(v) for v in row])
    print(f"  ✓ {len(INVENTORY)} inventory items written.")

    # ── Reservations ───────────────────────────────────────────────────────────
    print("Setting up Reservations tab…")
    try:
        res_ws = ss.worksheet("Reservations")
    except gspread.WorksheetNotFound:
        res_ws = ss.add_worksheet("Reservations", rows=500, cols=20)

    res_ws.clear()
    res_headers = [
        "Reservation ID", "Customer Name", "Customer Phone", "Customer Email",
        "Item IDs", "Start Date & Time", "End Date & Time", "Rental Type",
        "Payment Status", "Payment Amount", "Waiver Signed", "Reservation Status",
        "Notes", "Created At",
    ]
    res_ws.append_row(res_headers)
    for row in sample_reservations():
        res_ws.append_row([str(v) for v in row])
    print(f"  ✓ {len(sample_reservations())} sample reservations written.")

    print("\nDone! Open the app and you should see sample data.")


if __name__ == "__main__":
    main()
