# Pucon Kayak Retreat — Staff App

A simple app for managing kayak rentals: reservations, equipment inventory, check-out/check-in, and optional Google Sheets backup.

---

## What this app does

- Track reservations from booking to check-out to return
- Manage your fleet of kayaks, paddles, PFDs, and other gear
- See today's pickups and expected returns at a glance
- Optionally back up all data to a Google Sheet that you own

---

## Requirements

**Python 3.9 or later.** That's it.

To check if you already have Python: open Terminal (search "Terminal" in Spotlight) and type:
```
python3 --version
```
If it says `Python 3.9.x` or higher, you're good. If not, download it from **https://www.python.org/downloads/** — click the big yellow button, run the installer, and you're done.

---

## First-time setup (do this once)

1. Open **Terminal** (Cmd+Space, type "Terminal", press Enter)
2. Drag the app folder into the Terminal window — it will type the path for you
3. Press Enter to go into that folder, then run:

```
./setup.sh
```

This takes about a minute. It installs everything the app needs. You only need to do this once.

---

## Starting the app every day

**Option A — Double-click** `run.command` in Finder. The app opens in your browser automatically.

> The first time you double-click it, macOS may say "cannot be opened because it is from an unidentified developer." If that happens: right-click (or Control-click) the file → **Open** → **Open** again in the dialog. You only need to do this once.

**Option B — Terminal:**
```
./start.sh
```

The app opens at **http://localhost:5000** in your browser.

---

## Stopping the app

Go back to the Terminal window and press **Ctrl+C**. That's it.

---

## Logging in

The default PIN is **1234**. You can change it in **Settings → Change PIN**.

---

## Google Sheets backup (optional)

If you want your data backed up to a Google Sheet:

1. Go to **Settings** in the app
2. In the "Google Sheets" section, paste the link to your Google Sheet
3. Click **Save**
4. Click **↑ Push to Google Sheets** to export your current data

The app will automatically sync changes to the Sheet every 5 minutes, and you can manually pull Sheet changes back with **↓ Pull from Google Sheets**.

> **Note:** Google Sheets sync requires a `credentials.json` file from Google Cloud Console. Ask your setup person for this file and place it in the same folder as the app.

---

## Troubleshooting

**"Permission denied" when running setup.sh or start.sh**
```
chmod +x setup.sh start.sh run.command
```
Then try again.

**The app opens but shows an error about the database**
Delete the file `rental.db` in the app folder and restart. (This resets all data — only do this if you don't have any important bookings yet.)

**"Python not found" error**
Install Python 3.9+ from https://www.python.org/downloads/ and run `./setup.sh` again.

**Google Sheets sync isn't working**
- Make sure `credentials.json` is in the app folder
- Go to Settings and check that the Sheet link is saved correctly
- Check your internet connection
- Click **↓ Pull from Google Sheets** to retry manually

**The app stopped unexpectedly**
Just run `./start.sh` (or double-click `run.command`) again. Your data is saved automatically.

---

## Day-to-day workflow

1. Start the app (`run.command` or `./start.sh`)
2. The **Dashboard** shows today's pickups and expected returns
3. When a customer arrives, open their reservation → **Check Out**
4. When gear is returned, open the reservation → **Mark Returned**
5. Inventory status updates automatically
6. Stop the app when done (Ctrl+C in Terminal)

---

*Built for Pucon Kayak Retreat staff use.*
