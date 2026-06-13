# Pucon Kayak Retreat — Rental Tracker

A local web app for managing kayak rentals. Google Sheets acts as the database so staff can also view and edit data directly in the sheet.

Available as a **standalone Mac app** (no Python install needed) or as a Python script.

---

## Option A — Mac App (recommended)

### Install
1. Open `dist/PuconKayakRetreat.dmg`
2. Drag **Pucon Kayak Retreat** → **Applications**
3. **First launch only:** right-click the app → **Open** (bypasses macOS Gatekeeper for unsigned apps)

### Connect to Google Sheets
The app stores its config here — open Finder and press `⌘⇧G`, then type:
```
~/Library/Application Support/PuconKayakRetreat/
```
Two files go in that folder:

| File | What it is |
|---|---|
| `.env` | Created automatically on first launch — edit it with your Sheet ID and PIN |
| `credentials.json` | Your Google service account JSON — copy it here |

Edit `.env` with any text editor:
```
GOOGLE_SHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
GOOGLE_CREDENTIALS_FILE=credentials.json
APP_PIN=1234
```
Restart the app after editing.

### Rebuild the app (after code changes)
```bash
bash build.sh
```
Output is `dist/PuconKayakRetreat.dmg` (~31 MB).

---

## Option B — Run as Python script (development)

### 1. Install dependencies

```bash
cd pucon-kayak-retreat
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set up Google Sheets API credentials

#### Create a service account
1. Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → Credentials**.
2. Click **Create Credentials → Service account**. Give it any name.
3. On the service account page, go to **Keys → Add Key → Create new key → JSON**. Download the file.
4. Rename the downloaded file to `credentials.json` and place it in the `pucon-kayak-retreat/` folder.
5. Enable the **Google Sheets API** and **Google Drive API** for your project (search for them in **APIs & Services → Library**).

#### Share your Sheet with the service account
1. Open your Google Sheet.
2. Click **Share** and add the service account email (looks like `name@project-id.iam.gserviceaccount.com`) as an **Editor**.

### 3. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

```
GOOGLE_SHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms   # from the Sheet URL
GOOGLE_CREDENTIALS_FILE=credentials.json
APP_PIN=1234          # change to your preferred staff PIN
SECRET_KEY=some-random-string-here
```

The Sheet ID is the long string in the URL between `/d/` and `/edit`.

### 4. Set up your Google Sheet

Your sheet needs two tabs named exactly **`Inventory`** and **`Reservations`**.

**Option A — Sample data (recommended for testing):**
```bash
python setup_sample_data.py
```
This creates both tabs with headers and populates them with sample kayaks and reservations.

**Option B — Set up headers manually:**
Create the tabs yourself and add the headers from the sheet structure below.

### 5. Run the app

```bash
python app.py
```

Open **http://localhost:5000** in a browser. Enter your PIN to sign in.

---

## Google Sheet structure

### Inventory tab
| Column | Description |
|---|---|
| Item ID | Unique ID, e.g. `SKAYAK-01` |
| Category | `Single Kayak`, `Tandem Kayak`, `Paddle`, `PFD`, `Helmet`, `Dry Bag` |
| Name/Description | Human-readable name shown to staff |
| Status | `Available`, `Rented`, `Maintenance` |
| Condition Notes | Free-text condition notes |
| Hourly Rate | Price per hour ($) |
| Half-Day Rate | Price for a half-day ($) |
| Full-Day Rate | Price for a full day ($) |
| Multi-Day Rate | Price per day for multi-day rentals ($) |

### Reservations tab
| Column | Description |
|---|---|
| Reservation ID | Auto-generated, e.g. `RES-A1B2C3` |
| Customer Name | |
| Customer Phone | |
| Customer Email | |
| Item IDs | Comma-separated Item IDs, e.g. `SKAYAK-01, PADDLE-01, PFD-02` |
| Start Date & Time | ISO format: `2025-07-04T09:00` |
| End Date & Time | ISO format: `2025-07-04T17:00` |
| Rental Type | `Hourly`, `Half-Day`, `Full-Day`, `Multi-Day` |
| Payment Status | `Unpaid`, `Deposit Paid`, `Paid in Full` |
| Payment Amount | Dollar amount |
| Waiver Signed | `Yes` or `No` |
| Reservation Status | `Upcoming`, `Checked Out`, `Returned`, `Canceled` |
| Notes | Free text |
| Created At | Auto-set timestamp |

---

## Features

| Feature | Description |
|---|---|
| **Dashboard** | Today's pickups, expected returns, overdue items, fleet stats |
| **New Reservation** | Multi-item booking with live conflict detection and auto-pricing |
| **Reservations list** | Search by name/email/ID, filter by status/date |
| **Reservation detail** | View/edit details, check out, return (with condition notes), cancel |
| **Inventory** | Card view with quick status changes and condition note editing |
| **Calendar** | Month grid with day-click detail panel |

---

## Running on a tablet / other computers

The app binds to `0.0.0.0:5000` so any device on the same WiFi network can reach it at `http://<your-computer-ip>:5000`. Find your IP with `ifconfig` (Mac/Linux) or `ipconfig` (Windows).

---

## Troubleshooting

**"Credentials file not found"** — Make sure `credentials.json` is in the project folder and `GOOGLE_CREDENTIALS_FILE` in `.env` matches the filename.

**"Worksheet not found"** — Your sheet must have tabs named exactly `Inventory` and `Reservations` (case-sensitive). Run `setup_sample_data.py` to create them automatically.

**"Cannot connect to Google Sheets"** — Check that the service account email has been shared on the Sheet as an Editor, and that both Google Sheets API and Google Drive API are enabled in Google Cloud Console.

**Port already in use** — Change the port: `python app.py` → edit the last line of `app.py` to use a different port, e.g. `port=5001`.
