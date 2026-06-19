import os
import re
import sys
import csv
import io
import uuid
import json
import math
import stat
import signal
import logging
import tempfile
import hashlib
import secrets as _secrets
import base64
import webbrowser
import calendar as cal_module
import functools
import threading
import time
import subprocess
import urllib.request
import requests as _requests
from pathlib import Path
from collections import Counter
from datetime import datetime, date, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory,
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from utils import _parse_dt  # single source of truth
from db import DatabaseClient, DatabaseError
from sheets import SheetsClient, SheetsError, SCOPES
from sync import SheetsSyncer

log = logging.getLogger(__name__)

APP_VERSION = "1.3.5"

# OAuth2 over HTTP is fine for localhost (Desktop app running on the user's machine)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# ── Resolve base directory (handles PyInstaller bundle) ───────────────────────
# main.py sets PKR_BASE_DIR before importing this module when running bundled.
# Fallback: directory of this file (normal run).
_BASE = Path(os.environ.get("PKR_BASE_DIR", Path(__file__).parent))

# Load .env — main.py loads it first (from App Support dir), so this is a no-op
# when running bundled; it loads .env from CWD when running directly.
load_dotenv()

# Waiver uploads live alongside rental.db in App Support (created on first use)
_WAIVERS_DIR = Path(os.environ.get("PKR_DB_PATH", ".")) / "waivers"
_ALLOWED_WAIVER_EXT = {"pdf", "jpg", "jpeg", "png"}

app = Flask(
    __name__,
    template_folder=str(_BASE / "templates"),
    static_folder=str(_BASE / "static"),
)
app.secret_key = os.environ.get("SECRET_KEY", "pucon-kayak-dev-secret")
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12  # 12 hours

db = DatabaseClient()

# Categories that qualify for the extended-rental discount (#33 — deduplicated + normalised)
KAYAK_CATEGORIES = {
    'Dagger', 'Dagger Kayak', 'Jackson Kayak', 'Waka', 'Spade',
    'Pyranha', 'Liquid Logic', 'Liquidlogic', 'Perception',
}

# State-machine constants (module-level so they're readable and not rebuilt per-request)
VALID_STATUSES = {"Upcoming", "Checked Out", "Returned", "Canceled"}
VALID_TRANSITIONS = {
    "Upcoming":    {"Checked Out", "Canceled"},
    "Checked Out": {"Returned", "Canceled"},
    "Returned":    set(),   # terminal
    "Canceled":    set(),   # terminal
}

def _split_item_ids(s) -> list:
    """Split a comma-separated Item IDs string into a clean list (P-8)."""
    return [i.strip() for i in str(s or "").split(",") if i.strip()]


def _compute_item_availability(inventory, reservations, start_str, end_str, exclude_res_id=None) -> dict:
    """Return {item_id: units_available} for the given window, excluding one
    reservation's own usage (so its edit panel doesn't show its own gear as
    unavailable). Used to size the quantity steppers in the edit panel."""
    qty_map = {}
    for i in inventory:
        try:
            qty_map[str(i.get("Item ID"))] = 0 if i.get("Status") == "Maintenance" else int(i.get("Quantity") or 1)
        except (TypeError, ValueError):
            qty_map[str(i.get("Item ID"))] = 1

    start_dt = _parse_dt(start_str)
    end_dt = _parse_dt(end_str)
    booked = Counter()
    if start_dt and end_dt:
        for r in reservations:
            if r.get("Reservation Status") in ("Canceled", "Returned"):
                continue
            if exclude_res_id and str(r.get("Reservation ID")) == str(exclude_res_id):
                continue
            rs = _parse_dt(r.get("Start Date & Time", ""))
            re_ = _parse_dt(r.get("End Date & Time", ""))
            if not rs or not re_:
                continue
            if start_dt < re_ and end_dt > rs:
                booked.update(_split_item_ids(r.get("Item IDs", "")))

    return {iid: max(0, qty - booked.get(iid, 0)) for iid, qty in qty_map.items()}


def _fetch_clp_rate() -> float:
    """Fetch live USD→CLP exchange rate from open.er-api.com (free, no auth)."""
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(url, headers={"User-Agent": "PKR/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        return float(data["rates"]["CLP"])
    except Exception:
        # Fallback to stored rate or default
        stored = db.get_settings().get("exchange_rate_usd_clp", "")
        return float(stored) if stored else 950.0

def _get_clp_rate() -> float:
    """Return stored USD→CLP rate, defaulting to 950 if missing or zero (#10)."""
    settings = db.get_settings()
    stored = settings.get("exchange_rate_usd_clp", "")
    try:
        rate = float(stored)
        return rate if rate > 0 else 950.0
    except (TypeError, ValueError):
        return 950.0

def get_display_currency():
    """Returns 'USD' or 'CLP' based on session preference."""
    return session.get("display_currency", db.get_settings().get("default_currency", "USD"))

def get_rate_currency():
    """Returns the currency in which item rates are stored in the DB (default 'CLP')."""
    return db.get_settings().get("rate_currency", "CLP")

def pricing_enabled():
    """Returns True unless staff have explicitly turned pricing tracking off in Settings."""
    return db.get_settings().get("pricing_enabled", "1") != "0"

# Load startup settings for one-time init (sheet ID, OAuth token)
_startup_settings = db.get_settings()

# Load sheet ID from DB settings (overrides env var so it survives restarts)
_saved_sheet_id = _startup_settings.get("sheet_id", "")
if _saved_sheet_id:
    os.environ["GOOGLE_SHEET_ID"] = _saved_sheet_id

# Sheets sync is optional — silently disabled if credentials/sheet not configured
def _on_oauth_token_refresh(new_token_json):
    """Persist a refreshed OAuth token back to the DB and update the live client."""
    db.update_settings({"google_oauth_token": new_token_json})
    _sheets_client._oauth_token_json = new_token_json

_sheets_client = SheetsClient(
    credentials_file=os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
    sheet_id=_startup_settings.get("sheet_id", "") or os.environ.get("GOOGLE_SHEET_ID", ""),
    oauth_token_json=_startup_settings.get("google_oauth_token", "") or None,
    on_token_refresh=_on_oauth_token_refresh,
)
syncer = SheetsSyncer(_sheets_client)


_last_sync_sheet_id = ""

# OAuth PKCE verifiers stored server-side (keyed by state) so the system
# browser callback can retrieve them regardless of which session it has.
# Protected by a lock (F-15); entries expire after 10 minutes (TTL).
_oauth_verifiers_lock = threading.Lock()
_oauth_verifiers: dict = {}   # state → {"verifier": str, "expires": float}


def _prune_oauth_verifiers():
    """Remove expired PKCE verifiers (older than 10 minutes)."""
    now = time.time()
    with _oauth_verifiers_lock:
        expired = [k for k, v in _oauth_verifiers.items() if v["expires"] < now]
        for k in expired:
            del _oauth_verifiers[k]


def _background_pull_loop():
    """Pull from Google Sheets every 5 minutes in a background daemon thread."""
    time.sleep(30)  # brief startup delay
    while True:
        try:
            sheet_id = db.get_settings().get("sheet_id", "") or os.environ.get("GOOGLE_SHEET_ID", "")
            if sheet_id:
                _sheets_client._sheet_id = sheet_id
                _sheets_client._spreadsheet = None
                syncer.pull_from_sheets(db)
                db.update_settings({"last_synced": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                log.info("Background pull from Sheets complete.")
        except Exception as e:
            log.warning("Background Sheets pull failed (non-fatal): %s", e)
        time.sleep(300)  # 5 minutes


_pull_thread = threading.Thread(target=_background_pull_loop, daemon=True)
_pull_thread.start()  # log is now defined at module top — no ordering issue (#5)


def _sync():
    """Push to Sheets in background after every write."""
    global _last_sync_sheet_id
    sheet_id = db.get_settings().get("sheet_id", "") or os.environ.get("GOOGLE_SHEET_ID", "")
    if sheet_id:
        if sheet_id != _last_sync_sheet_id:
            # Sheet ID changed — force reconnect
            _sheets_client._sheet_id = sheet_id
            _sheets_client._spreadsheet = None
            _last_sync_sheet_id = sheet_id
        syncer.push_async(db)


# ── Auth decorator ────────────────────────────────────────────────────────────

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        app_pin = db.get_settings().get("app_pin", "") or os.environ.get("APP_PIN", "1234")
        if request.form.get("pin") == app_pin:
            session["authenticated"] = True
            session.permanent = True
            # D-3: validate next param to prevent open redirect
            next_url = request.args.get("next", "")
            if not next_url or not next_url.startswith("/"):
                next_url = url_for("dashboard")
            return redirect(next_url)
        error = "Incorrect PIN — please try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    view = request.args.get("view", "month")
    sheets_error = None
    today = date.today()

    # ── Shared inventory counts ────────────────────────────────────────────────
    total_items = rented_items = available_items = maintenance_items = 0

    # ── Day-view data ──────────────────────────────────────────────────────────
    today_pickups = []
    today_returns = []
    overdue = []

    # ── Week-view data ─────────────────────────────────────────────────────────
    week_days = []  # list of dicts: {date, label, pickups, returns, reservations}

    # ── Month-view data ────────────────────────────────────────────────────────
    month_stats = {}   # total_reservations, busiest_day, items_rented, utilization
    month_days = []    # list of {date, label, count}

    try:
        reservations = db.get_reservations()
        inventory = db.get_inventory()

        total_items = len(inventory)
        rented_items = sum(1 for i in inventory if i.get("Status") == "Rented")
        available_items = sum(1 for i in inventory if i.get("Status") == "Available")
        maintenance_items = sum(1 for i in inventory if i.get("Status") == "Maintenance")

        active_reservations = [
            r for r in reservations
            if r.get("Reservation Status", "") not in ("Canceled", "Returned")
        ]

        # Day view
        for r in active_reservations:
            status = r.get("Reservation Status", "")
            start = _parse_dt(r.get("Start Date & Time", ""))
            end = _parse_dt(r.get("End Date & Time", ""))

            if start and start.date() == today and status == "Upcoming":
                today_pickups.append(r)
            if end:
                if end.date() == today and status == "Checked Out":
                    today_returns.append(r)
                elif end.date() < today and status == "Checked Out":
                    overdue.append(r)

        # Week view
        if view == "week":
            for offset in range(7):
                day = today + timedelta(days=offset)
                day_pickups = []
                day_returns = []
                day_reservations = []
                for r in active_reservations:
                    status = r.get("Reservation Status", "")
                    start = _parse_dt(r.get("Start Date & Time", ""))
                    end = _parse_dt(r.get("End Date & Time", ""))
                    is_pickup = start and start.date() == day and status == "Upcoming"
                    is_return = end and end.date() == day and status == "Checked Out"
                    if is_pickup:
                        day_pickups.append(r)
                    if is_return:
                        day_returns.append(r)
                    if is_pickup or is_return:
                        day_reservations.append(r)
                label = "Today" if day == today else day.strftime("%a %-d")
                week_days.append({
                    "date": day,
                    "label": label,
                    "full_label": day.strftime("%A, %B %-d"),
                    "pickups": len(day_pickups),
                    "returns": len(day_returns),
                    "count": len(day_reservations),
                    "is_today": day == today,
                })

        # Month view
        if view == "month":
            year, month = today.year, today.month
            _, days_in_month = cal_module.monthrange(year, month)
            month_start = date(year, month, 1)
            month_end = date(year, month, days_in_month)

            day_counts = {}
            item_ids_this_month = set()
            total_month_res = 0

            for r in reservations:  # include all statuses for history
                status = r.get("Reservation Status", "")
                if status == "Canceled":
                    continue
                start = _parse_dt(r.get("Start Date & Time", ""))
                end = _parse_dt(r.get("End Date & Time", ""))
                if not start:
                    continue
                # Count reservation if it overlaps the month at all
                r_start = start.date()
                r_end = end.date() if end else r_start
                if r_end < month_start or r_start > month_end:
                    continue
                total_month_res += 1
                # Attribute to start day (within this month)
                attr_day = max(r_start, month_start)
                day_counts[attr_day] = day_counts.get(attr_day, 0) + 1
                # Collect item IDs
                raw_ids = r.get("Item IDs", "")
                for iid in str(raw_ids).split(","):
                    iid = iid.strip()
                    if iid:
                        item_ids_this_month.add(iid)

            busiest_day = None
            busiest_count = 0
            for d, cnt in day_counts.items():
                if cnt > busiest_count:
                    busiest_count = cnt
                    busiest_day = d

            utilization = round(len(item_ids_this_month) / total_items * 100) if total_items else 0

            month_stats = {
                "total_reservations": total_month_res,
                "busiest_day": busiest_day.strftime("%b %-d") if busiest_day else "—",
                "busiest_count": busiest_count,
                "items_rented": len(item_ids_this_month),
                "utilization": utilization,
                "month_label": today.strftime("%B %Y"),
            }

            for d in range(1, days_in_month + 1):
                day = date(year, month, d)
                count = day_counts.get(day, 0)
                month_days.append({
                    "date": day,
                    "day_num": d,
                    "label": day.strftime("%a"),
                    "count": count,
                    "is_today": day == today,
                    "is_past": day < today,
                })

    except (DatabaseError, SheetsError) as e:
        sheets_error = str(e)

    # Build compact reservation list for the JS calendar (all non-canceled)
    try:
        _all_res = db.get_reservations()
    except Exception:
        _all_res = []
    calendar_reservations = [
        {
            "id":     r.get("Reservation ID", ""),
            "customer": r.get("Customer Name", "—"),
            "start":  r.get("Start Date & Time", ""),
            "end":    r.get("End Date & Time", ""),
            "status": r.get("Reservation Status", ""),
            "items":  r.get("Item IDs", ""),
            "type":   r.get("Rental Type", ""),
        }
        for r in _all_res
        if r.get("Reservation Status", "") != "Canceled"
    ]
    # Item ID → display name lookup, used by the dashboard calendar to show
    # which specific equipment is reserved on a given day.
    try:
        item_name_map = {
            i.get("Item ID", ""): i.get("Name/Description") or i.get("Item ID", "")
            for i in db.get_inventory()
        }
    except Exception:
        item_name_map = {}

    return render_template(
        "dashboard.html",
        view=view,
        today=today,
        # day view
        today_pickups=today_pickups,
        today_returns=today_returns,
        overdue=overdue,
        # inventory
        total_items=total_items,
        rented_items=rented_items,
        available_items=available_items,
        maintenance_items=maintenance_items,
        # week view
        week_days=week_days,
        # month view (server-side fallback, JS calendar uses calendar_reservations)
        month_stats=month_stats,
        month_days=month_days,
        calendar_reservations=calendar_reservations,
        item_name_map=item_name_map,
        sheets_error=sheets_error,
    )


# ── Reservations list ─────────────────────────────────────────────────────────

@app.route("/reservations")
@login_required
def reservations():
    sheets_error = None
    filtered = []
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")
    date_filter = request.args.get("date", "")

    try:
        all_res = db.get_reservations()
        filtered = all_res

        if q:
            def _norm(s): return s.lower().replace("-", " ").replace("_", " ")
            ql = _norm(q)
            filtered = [
                r for r in filtered
                if ql in _norm(r.get("Customer Name", ""))
                or ql in _norm(r.get("Customer Email", ""))
                or ql in _norm(r.get("Customer Phone", ""))
                or ql in _norm(r.get("Reservation ID", ""))
                or ql in _norm(r.get("Item IDs", ""))
            ]
        if status_filter:
            filtered = [r for r in filtered if r.get("Reservation Status") == status_filter]
        if date_filter:
            # F-1: check if the selected date falls *within* the reservation's range,
            # not just a string-prefix match on start/end.
            try:
                filter_dt = datetime.strptime(date_filter, "%Y-%m-%d").date()
                def _overlaps(r):
                    s = _parse_dt(r.get("Start Date & Time", ""))
                    e = _parse_dt(r.get("End Date & Time", ""))
                    if not s:
                        return False
                    s_date = s.date()
                    e_date = e.date() if e else s_date
                    return s_date <= filter_dt <= e_date
                filtered = [r for r in filtered if _overlaps(r)]
            except ValueError:
                # Fallback to prefix match if the date string is malformed
                filtered = [
                    r for r in filtered
                    if str(r.get("Start Date & Time", "")).startswith(date_filter)
                    or str(r.get("End Date & Time", "")).startswith(date_filter)
                ]

        filtered.sort(key=lambda r: r.get("Start Date & Time", ""), reverse=True)

    except (DatabaseError, SheetsError) as e:
        sheets_error = str(e)

    return render_template(
        "reservations.html",
        reservations=filtered,
        q=q,
        status_filter=status_filter,
        date_filter=date_filter,
        sheets_error=sheets_error,
    )


# ── Shared form validation helper (items 34-35) ───────────────────────────────

def _reconstruct_item_ids_from_qty_form(form) -> list:
    """Rebuild a flat (possibly repeated) item_ids list from the edit panel's
    quantity-stepper inputs: parallel item_qty_id / item_qty_val lists."""
    ids = form.getlist("item_qty_id")
    vals = form.getlist("item_qty_val")
    item_ids = []
    for iid, val in zip(ids, vals):
        try:
            qty = max(0, int(val))
        except (TypeError, ValueError):
            qty = 0
        item_ids.extend([iid] * qty)
    return item_ids


def _validate_reservation_form(form, item_ids=None):
    """Validate the fields common to both 'create' and 'edit' reservation forms.

    Returns (errors, parsed) where:
      errors  — list of human-readable error strings (empty = valid)
      parsed  — dict with start_str, end_str, start_dt, end_dt, pay_amt
    """
    errors = []
    start_str = form.get("start_datetime", "").strip()
    end_str   = form.get("end_datetime",   "").strip()

    if item_ids is None:
        item_ids = form.getlist("item_ids")
    if not item_ids:
        errors.append("Select at least one item.")
    if not start_str:
        errors.append("Start date/time is required.")
    if not end_str:
        errors.append("End date/time is required.")

    start_dt = _parse_dt(start_str) if start_str else None
    end_dt   = _parse_dt(end_str)   if end_str   else None

    if start_str and not start_dt:
        errors.append("Start date/time is not a valid date.")
    if end_str and not end_dt:
        errors.append("End date/time is not a valid date.")
    if start_dt and end_dt and end_dt <= start_dt:
        errors.append("End time must be after start time.")

    pay_amt = 0.0
    try:
        pay_amt = float(form.get("payment_amount", "0") or "0")
        if pay_amt < 0:
            errors.append("Payment amount cannot be negative.")
    except ValueError:
        errors.append("Payment amount must be a number.")

    parsed = {
        "start_str": start_str, "end_str": end_str,
        "start_dt": start_dt,   "end_dt": end_dt,
        "pay_amt": pay_amt,
    }
    return errors, parsed


# ── New reservation ───────────────────────────────────────────────────────────

@app.route("/reservations/new", methods=["GET", "POST"])
@login_required
def new_reservation():
    try:
        inventory = db.get_inventory()
        reservations_raw = db.get_reservations()
    except DatabaseError as e:
        flash(f"Cannot load inventory: {e}", "error")
        return redirect(url_for("dashboard"))

    # Pass active reservations to template for client-side conflict display
    active_reservations = [
        {
            "start": r.get("Start Date & Time", ""),
            "end":   r.get("End Date & Time", ""),
            "status": r.get("Reservation Status", ""),
            "items":  r.get("Item IDs", ""),
        }
        for r in reservations_raw
        if r.get("Reservation Status") not in ("Canceled", "Returned")
    ]

    form_data = {}

    if request.method == "POST":
        form_data = request.form.to_dict()
        item_ids = request.form.getlist("item_ids")

        errors, parsed = _validate_reservation_form(request.form)
        start_str = parsed["start_str"]
        end_str   = parsed["end_str"]

        if not request.form.get("customer_name", "").strip():
            errors.append("Customer name is required.")

        if not errors:
            try:
                # D-1: force_refresh bypasses the 5s cache so a concurrent booking
                # cannot slip through the conflict check window.
                conflicts = db.check_conflicts(item_ids, start_str, end_str, force_refresh=True)
                if conflicts:
                    names = ", ".join(c.get("Customer Name", "?") for c in conflicts)
                    errors.append(
                        f"Booking conflict: one or more items are already reserved by {names} during this time."
                    )
            except (DatabaseError, SheetsError) as e:
                errors.append(str(e))

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("new_reservation.html", inventory=inventory, form_data=form_data,
                                   item_ids=item_ids, active_reservations=active_reservations)

        try:
            res_id = "RES-" + uuid.uuid4().hex[:6].upper()
            row = {
                "Reservation ID": res_id,
                "Customer Name": request.form.get("customer_name", "").strip(),
                "Customer Phone": request.form.get("customer_phone", "").strip(),
                "Customer Email": request.form.get("customer_email", "").strip(),
                "Item IDs": ", ".join(item_ids),
                "Start Date & Time": start_str,
                "End Date & Time": end_str,
                "Rental Type": request.form.get("rental_type", ""),
                "Payment Status": request.form.get("payment_status", "Unpaid"),
                "Payment Amount": request.form.get("payment_amount", "0"),
                "Waiver Signed": "Yes" if request.form.get("waiver_signed") else "No",
                "Reservation Status": "Upcoming",
                "Notes": request.form.get("notes", "").strip(),
                "Created At": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            db.add_reservation(row)
            _sync()
            flash(f"Reservation {res_id} created successfully!", "success")
            return redirect(url_for("reservation_detail", res_id=res_id))
        except SheetsError as e:
            flash(f"Error saving reservation: {e}", "error")

    return render_template("new_reservation.html", inventory=inventory, form_data=form_data,
                           item_ids=[], active_reservations=active_reservations)


# ── Discount helper (F-6: single source of truth for tier logic) ──────────────

def _kayak_discount_pct(reservation: dict, reserved_items: list) -> int:
    """Return the applicable discount percentage for a kayak reservation, or 0."""
    has_kayak = any(i.get("Category", "") in KAYAK_CATEGORIES for i in reserved_items)
    if not has_kayak:
        return 0
    s = _parse_dt(reservation.get("Start Date & Time", ""))
    e = _parse_dt(reservation.get("End Date & Time", ""))
    if not s or not e:
        return 0
    days = (e - s).days
    if days < 10:   return 0
    if days <= 15:  return 5
    if days <= 20:  return 10
    if days <= 25:  return 15
    if days <= 30:  return 20
    return 25


# ── Reservation detail ────────────────────────────────────────────────────────

@app.route("/reservations/<res_id>", methods=["GET", "POST"])
@login_required
def reservation_detail(res_id):
    try:
        all_res = db.get_reservations()
        reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
        if not reservation:
            flash("Reservation not found.", "error")
            return redirect(url_for("reservations"))

        inventory = db.get_inventory()

        if request.method == "POST":
            action = request.form.get("action")

            if action == "update_status":
                new_status = request.form.get("status")
                current_status = reservation.get("Reservation Status", "")

                # Allowlist + state-machine guard (constants defined at module level)
                if new_status not in VALID_STATUSES:
                    flash(f"Invalid status '{new_status}'.", "error")
                    return redirect(url_for("reservation_detail", res_id=res_id))
                if new_status not in VALID_TRANSITIONS.get(current_status, set()):
                    flash(f"Cannot move from '{current_status}' to '{new_status}'.", "error")
                    return redirect(url_for("reservation_detail", res_id=res_id))

                updates = {"Reservation Status": new_status}
                item_ids = _split_item_ids(reservation.get("Item IDs", ""))

                if new_status == "Checked Out":
                    for iid in item_ids:
                        db.update_inventory_item(iid, {"Status": "Rented"})

                elif new_status == "Returned":
                    condition_notes = request.form.get("condition_notes", "").strip()
                    existing_notes = str(reservation.get("Notes", ""))
                    if condition_notes:
                        updates["Notes"] = (existing_notes + f"\n[Return condition: {condition_notes}]").strip()
                    for iid in item_ids:
                        item_updates = {"Status": "Available"}
                        if condition_notes:
                            item_updates["Condition Notes"] = condition_notes
                        db.update_inventory_item(iid, item_updates)

                elif new_status == "Canceled":
                    # Use fresh inventory to avoid stale-cache decisions
                    fresh_inventory = db.get_inventory(force_refresh=True)
                    for iid in item_ids:
                        item = next((i for i in fresh_inventory if str(i.get("Item ID")) == iid), None)
                        if item and item.get("Status") == "Rented":
                            db.update_inventory_item(iid, {"Status": "Available"})

                # Conditional update: only applies if DB status is still current_status.
                # Prevents double-click/concurrent-POST race (#6).
                if not db.update_reservation_conditional(res_id, current_status, updates):
                    flash(
                        "This reservation was already updated by another request — "
                        "please refresh the page before trying again.",
                        "warning",
                    )
                    return redirect(url_for("reservation_detail", res_id=res_id))
                _sync()
                flash(f"Status updated to {new_status}.", "success")

            elif action == "update_details":
                item_ids = _reconstruct_item_ids_from_qty_form(request.form)
                edit_errors, parsed = _validate_reservation_form(request.form, item_ids=item_ids)
                start_str = parsed["start_str"]
                end_str   = parsed["end_str"]
                pay_amt   = parsed["pay_amt"]

                if not edit_errors:
                    conflicts = db.check_conflicts(item_ids, start_str, end_str, exclude_id=res_id, force_refresh=True)
                    if conflicts:
                        names = ", ".join(c.get("Customer Name", "?") for c in conflicts)
                        edit_errors.append(f"Booking conflict with {names}.")

                if edit_errors:
                    for err in edit_errors:
                        flash(err, "error")
                    # F-2: re-render with submitted values so nothing is lost; edit panel stays open
                    all_res2 = db.get_reservations(force_refresh=True)
                    res2 = next((r for r in all_res2 if str(r.get("Reservation ID")) == res_id), reservation)
                    inv2 = db.get_inventory()
                    item_ids2 = _split_item_ids(res2.get("Item IDs",""))
                    reserved_items2 = [i for i in inv2 if str(i.get("Item ID")) in item_ids2]
                    # Preserve the staff member's attempted equipment quantities (not
                    # the saved ones) so the steppers still show what they typed.
                    edit_item_qtys = dict(Counter(item_ids))
                    item_availability2 = _compute_item_availability(
                        inv2, all_res2, start_str or res2.get("Start Date & Time",""),
                        end_str or res2.get("End Date & Time",""), exclude_res_id=res_id,
                    )
                    return render_template(
                        "reservation_detail.html",
                        reservation=res2,
                        inventory=inv2,
                        reserved_items=reserved_items2,
                        item_ids=item_ids2,
                        item_availability=item_availability2,
                        edit_item_qtys=edit_item_qtys,
                        discount_pct=0, discount_days=0, has_kayak_items=False,
                        edit_open=True,
                        edit_form_data=request.form.to_dict(),
                    )

                try:
                    updates = {
                        "Customer Name": request.form.get("customer_name", "").strip(),
                        "Customer Phone": request.form.get("customer_phone", "").strip(),
                        "Customer Email": request.form.get("customer_email", "").strip(),
                        "Item IDs": ", ".join(item_ids),
                        "Start Date & Time": start_str,
                        "End Date & Time": end_str,
                        "Rental Type": request.form.get("rental_type", ""),
                        "Waiver Signed": "Yes" if request.form.get("waiver_signed") else "No",
                        "Notes": request.form.get("notes", "").strip(),
                    }
                    # Payment fields are hidden from the form when pricing is
                    # disabled — only touch them if the form actually sent them.
                    if "payment_status" in request.form:
                        updates["Payment Status"] = request.form.get("payment_status", "Unpaid")
                    if "payment_amount" in request.form:
                        updates["Payment Amount"] = str(pay_amt)
                    db.update_reservation(res_id, updates)
                    _sync()
                    flash("Reservation updated.", "success")
                except DatabaseError as e:
                    flash(f"Error saving reservation: {e}", "error")

            return redirect(url_for("reservation_detail", res_id=res_id))

        # Reload after any updates — guard against None if a Sheets pull raced us (#1)
        all_res = db.get_reservations(force_refresh=True)
        reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
        if not reservation:
            flash("Reservation no longer found — it may have been removed.", "error")
            return redirect(url_for("reservations"))
        item_ids = _split_item_ids(reservation.get("Item IDs", ""))
        reserved_items = [i for i in inventory if str(i.get("Item ID")) in item_ids]
        item_availability = _compute_item_availability(
            inventory, all_res,
            reservation.get("Start Date & Time", ""), reservation.get("End Date & Time", ""),
            exclude_res_id=res_id,
        )

        # F-6: discount tier in a single shared helper (also used by apply_discount)
        discount_pct = _kayak_discount_pct(reservation, reserved_items)
        discount_days = 0
        has_kayak_items = any(i.get("Category", "") in KAYAK_CATEGORIES for i in reserved_items)
        if has_kayak_items:
            s = _parse_dt(reservation.get("Start Date & Time", ""))
            e = _parse_dt(reservation.get("End Date & Time", ""))
            if s and e:
                discount_days = (e - s).days

        return render_template(
            "reservation_detail.html",
            reservation=reservation,
            inventory=inventory,
            reserved_items=reserved_items,
            item_ids=item_ids,
            item_availability=item_availability,
            edit_item_qtys=dict(Counter(item_ids)),
            discount_pct=discount_pct,
            discount_days=discount_days,
            has_kayak_items=has_kayak_items,
            edit_open=False,
            edit_form_data={},
        )

    except (DatabaseError, SheetsError) as e:
        flash(f"Error: {e}", "error")
        return redirect(url_for("reservations"))


# ── Inventory ─────────────────────────────────────────────────────────────────

@app.route("/inventory")
@login_required
def inventory_view():
    sheets_error = None
    items = []
    categories = []
    category_filter = request.args.get("category", "")
    status_filter = request.args.get("status", "")

    try:
        all_items = db.get_inventory()
        categories = sorted({i.get("Category", "") for i in all_items if i.get("Category")})

        items = all_items
        if category_filter:
            items = [i for i in items if i.get("Category") == category_filter]
        if status_filter:
            items = [i for i in items if i.get("Status") == status_filter]

    except (DatabaseError, SheetsError) as e:
        sheets_error = str(e)

    settings = db.get_settings()
    return render_template(
        "inventory.html",
        items=items,
        categories=categories,
        category_filter=category_filter,
        status_filter=status_filter,
        sheets_error=sheets_error,
        settings=settings,
    )


@app.route("/inventory/add", methods=["POST"])
@login_required
def add_equipment():
    try:
        item_id = request.form.get("item_id", "").strip()
        if not item_id:
            flash("Item ID is required.", "error")
            return redirect(url_for("inventory_view"))
        if "," in item_id:
            flash("Item ID cannot contain a comma.", "error")
            return redirect(url_for("inventory_view"))
        existing = db.get_inventory()
        if any(str(i.get("Item ID")) == item_id for i in existing):
            flash(f"Item ID '{item_id}' already exists. Use a unique ID.", "error")
            return redirect(url_for("inventory_view"))

        quantity = request.form.get("quantity", "1").strip() or "1"
        item = {
            "Item ID":          item_id,
            "Category":         request.form.get("category", "").strip(),
            "Name/Description": request.form.get("name", "").strip() or item_id,
            "Size":             request.form.get("size", ""),
            "Status":           request.form.get("status", "Available"),
            "Condition Notes":  request.form.get("condition_notes", "").strip(),
            "Hourly Rate":      request.form.get("hourly_rate", ""),
            "Half-Day Rate":    request.form.get("half_day_rate", ""),
            "Full-Day Rate":    request.form.get("full_day_rate", ""),
            "Multi-Day Rate":   request.form.get("multi_day_rate", ""),
            "Quantity":         quantity,
        }
        db.add_inventory_item(item)
        _sync()
        flash(f"Equipment '{item_id}' added successfully.", "success")
    except DatabaseError as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("inventory_view"))


@app.route("/inventory/<path:item_id>/delete", methods=["POST"])
@login_required
def delete_inventory(item_id):
    try:
        # Guard: block deletion if item is referenced in any active reservation
        active_statuses = {"Upcoming", "Checked Out"}
        active_refs = [
            r for r in db.get_reservations()
            if r.get("Reservation Status") in active_statuses
            and item_id in _split_item_ids(r.get("Item IDs",""))
        ]
        if active_refs:
            res_ids = ", ".join(r.get("Reservation ID", "?") for r in active_refs)
            flash(f"Cannot delete '{item_id}' — it is part of active reservation(s): {res_ids}.", "error")
            return redirect(url_for("inventory_view"))
        db.delete_inventory_item(item_id)
        _sync()
        flash(f"Item '{item_id}' removed from inventory.", "success")
    except DatabaseError as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("inventory_view"))


@app.route("/inventory/import", methods=["POST"])
@login_required
def import_inventory():
    """Bulk-import inventory from pasted spreadsheet data (tab-separated or CSV).

    The first row must be a header row. Column names are matched case-insensitively
    against our canonical names plus common aliases, so data pasted straight from
    Google Sheets, Excel, or Numbers works without any reformatting.
    """
    raw = request.form.get("paste_data", "").strip()
    if not raw:
        flash("No data pasted — nothing to import.", "error")
        return redirect(url_for("inventory_view"))

    # Detect delimiter: tab (spreadsheet copy) or comma (CSV)
    lines = raw.splitlines()
    delimiter = "\t" if "\t" in lines[0] else ","

    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)

    # Flexible column aliases → canonical DB column
    ALIASES = {
        # Item ID
        "item id": "Item ID", "id": "Item ID", "combined label": "Item ID",
        "item": "Item ID", "sku": "Item ID",
        # Category
        "category": "Category", "brand": "Category", "type": "Category",
        "category / brand": "Category", "category/brand": "Category",
        # Name
        "name/description": "Name/Description", "name": "Name/Description",
        "description": "Name/Description", "full model": "Name/Description",
        "model": "Name/Description", "item name": "Name/Description",
        # Size
        "size": "Size",
        # Status
        "status": "Status",
        # Condition Notes
        "condition notes": "Condition Notes", "notes": "Condition Notes",
        "condition": "Condition Notes",
        # Rates
        "hourly rate": "Hourly Rate", "hourly": "Hourly Rate",
        "half-day rate": "Half-Day Rate", "half day rate": "Half-Day Rate",
        "half day": "Half-Day Rate", "halfday": "Half-Day Rate",
        "full-day rate": "Full-Day Rate", "full day rate": "Full-Day Rate",
        "full day": "Full-Day Rate", "fullday": "Full-Day Rate", "daily rate": "Full-Day Rate",
        "multi-day rate": "Multi-Day Rate", "multi day rate": "Multi-Day Rate",
        "multi day": "Multi-Day Rate", "multiday": "Multi-Day Rate",
        "multi-day / day": "Multi-Day Rate", "multi day / day": "Multi-Day Rate",
        # Quantity
        "quantity": "Quantity", "qty": "Quantity", "count": "Quantity",
    }

    imported = skipped = 0
    errors = []
    existing_ids = {str(i.get("Item ID")) for i in db.get_inventory()}

    # F-14: Check that the header contains at least one recognizable Item ID column
    # before processing any rows.
    if reader.fieldnames:
        has_id_col = any(
            ALIASES.get((f or "").strip().lower()) == "Item ID"
            for f in reader.fieldnames
        )
        if not has_id_col:
            flash(
                "No 'Item ID' column found in the pasted data. "
                "Make sure your first row is a header row with column names.",
                "error",
            )
            return redirect(url_for("inventory_view"))

    for row_num, row in enumerate(reader, start=2):
        # Map incoming column names → canonical names
        mapped = {}
        for raw_col, value in row.items():
            if raw_col is None:
                continue
            canonical = ALIASES.get(raw_col.strip().lower())
            if canonical:
                mapped[canonical] = (value or "").strip()

        item_id = mapped.get("Item ID", "").strip()
        if not item_id:
            errors.append(f"Row {row_num}: skipped — no Item ID.")
            skipped += 1
            continue
        if "," in item_id:
            errors.append(f"Row {row_num}: skipped — Item ID '{item_id}' contains a comma.")
            skipped += 1
            continue

        # Default Status if missing/blank
        if not mapped.get("Status"):
            mapped["Status"] = "Available"
        # Default Name if missing
        if not mapped.get("Name/Description"):
            mapped["Name/Description"] = item_id
        # Default Quantity
        if not mapped.get("Quantity"):
            mapped["Quantity"] = "1"

        try:
            if item_id in existing_ids:
                # Update existing item with any provided fields
                update_fields = {k: v for k, v in mapped.items() if k != "Item ID" and v != ""}
                if update_fields:
                    db.update_inventory_item(item_id, update_fields)
                    imported += 1
            else:
                db.add_inventory_item(mapped)
                existing_ids.add(item_id)
                imported += 1
        except Exception as e:
            errors.append(f"Row {row_num} ({item_id}): {e}")
            skipped += 1

    if imported:
        _sync()
        flash(f"✓ Imported {imported} item{'s' if imported != 1 else ''}."
              + (f" {skipped} skipped." if skipped else ""), "success")
    else:
        flash(f"Nothing imported. {skipped} row{'s' if skipped != 1 else ''} skipped.", "error")

    if errors:
        for err in errors[:5]:   # cap at 5 to avoid flooding
            flash(err, "warning")

    return redirect(url_for("inventory_view"))


@app.route("/inventory/<path:item_id>/update", methods=["POST"])
@login_required
def update_inventory(item_id):
    try:
        updates = {}
        status = request.form.get("status")
        # Allowlist status values (#20)
        if status and status in {"Available", "Rented", "Maintenance"}:
            updates["Status"] = status
        # Use key-presence check (not truthiness) so notes can be cleared to "" (#19)
        if "condition_notes" in request.form:
            updates["Condition Notes"] = request.form.get("condition_notes", "")
        quantity = request.form.get("quantity")
        if quantity is not None and quantity != "":
            try:
                qty_int = int(quantity)
                if qty_int <= 0:
                    raise ValueError
                updates["Quantity"] = str(qty_int)
            except (ValueError, TypeError):
                flash("Quantity must be a positive whole number.", "error")
                return redirect(url_for("inventory_view"))
        # Per-item rates — allow blank to clear a rate
        for field, col in [
            ("hourly_rate",    "Hourly Rate"),
            ("half_day_rate",  "Half-Day Rate"),
            ("full_day_rate",  "Full-Day Rate"),
            ("multi_day_rate", "Multi-Day Rate"),
        ]:
            if field in request.form:
                updates[col] = request.form.get(field, "").strip()
        # Name/Description and Category from edit modal (F-4)
        name_val = request.form.get("name", "").strip()
        if name_val:
            updates["Name/Description"] = name_val
        category_val = request.form.get("category", "").strip()
        if category_val:
            updates["Category"] = category_val
        if updates:
            db.update_inventory_item(item_id, updates)
            _sync()
            flash(f"Item {item_id} updated.", "success")
    except DatabaseError as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("inventory_view"))


# ── Calendar ──────────────────────────────────────────────────────────────────

@app.route("/calendar")
@login_required
def calendar():
    sheets_error = None
    events = []
    try:
        reservations = db.get_reservations()
        events = [
            {
                "id": r.get("Reservation ID"),
                "customer": r.get("Customer Name"),
                "start": str(r.get("Start Date & Time", "")),
                "end": str(r.get("End Date & Time", "")),
                "status": r.get("Reservation Status"),
                "items": r.get("Item IDs"),
                "rental_type": r.get("Rental Type"),
            }
            for r in reservations
            if r.get("Reservation Status") not in ("Canceled",)
        ]
    except (DatabaseError, SheetsError) as e:
        sheets_error = str(e)

    return render_template("calendar.html", cal_events=events, sheets_error=sheets_error)


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/availability")
@login_required
def api_availability():
    try:
        raw = request.args.get("items", "")
        item_ids = _split_item_ids(raw)
        start_str = request.args.get("start", "")
        end_str = request.args.get("end", "")
        exclude_id = request.args.get("exclude", "")

        if not item_ids or not start_str or not end_str:
            return jsonify({"conflicts": []})

        conflicts = db.check_conflicts(item_ids, start_str, end_str, exclude_id=exclude_id or None)
        return jsonify({
            "conflicts": [
                {
                    "reservation_id": c.get("Reservation ID"),
                    "customer": c.get("Customer Name"),
                    "start": c.get("Start Date & Time"),
                    "end": c.get("End Date & Time"),
                    "items": c.get("Item IDs"),
                }
                for c in conflicts
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pricing")
@login_required
def api_pricing():
    try:
        raw = request.args.get("items", "")
        item_ids = _split_item_ids(raw)  # may contain repeated IDs (qty > 1)
        item_counts = Counter(item_ids)
        rental_type = request.args.get("type", "Hourly")
        start_str = request.args.get("start", "")
        end_str = request.args.get("end", "")

        inventory = db.get_inventory()
        inv_by_id = {str(i.get("Item ID")): i for i in inventory}
        items = [inv_by_id[iid] for iid in item_counts if iid in inv_by_id]

        rate_key_map = {
            "Hourly": "Hourly Rate",
            "Half-Day": "Half-Day Rate",
            "Full-Day": "Full-Day Rate",
            "Multi-Day": "Multi-Day Rate",
        }
        default_key_map = {
            "Hourly": "default_hourly_rate",
            "Half-Day": "default_half_day_rate",
            "Full-Day": "default_full_day_rate",
            "Multi-Day": "default_multi_day_rate",
        }
        rate_key = rate_key_map.get(rental_type, "Hourly Rate")
        settings = db.get_settings()
        try:
            default_rate = float(settings.get(default_key_map.get(rental_type, ""), "") or 0)
        except (ValueError, TypeError):
            default_rate = 0.0

        total = 0.0
        breakdown = []
        duration_hours = 0.0
        duration_days = 0

        start_dt = _parse_dt(start_str)
        end_dt = _parse_dt(end_str)
        if start_dt and end_dt and end_dt > start_dt:
            delta = end_dt - start_dt
            duration_hours = delta.total_seconds() / 3600
            duration_days = max(1, math.ceil(duration_hours / 24))

        for item in items:
            try:
                rate = float(item.get(rate_key, 0) or 0)
            except (ValueError, TypeError):
                rate = 0.0

            # Smarter fallback chain — avoids the stale-default-rate bug:
            # For Multi-Day: if no multi-day rate set, use the item's own Full-Day Rate
            # (much safer than a global default that may be stale or in wrong currency).
            # For all types: only fall through to the global default as a last resort.
            if rate == 0.0 and rental_type == "Multi-Day":
                try:
                    rate = float(item.get("Full-Day Rate", 0) or 0)
                except (ValueError, TypeError):
                    rate = 0.0
            if rate == 0.0 and rental_type == "Half-Day":
                try:
                    rate = float(item.get("Full-Day Rate", 0) or 0) * 0.5
                except (ValueError, TypeError):
                    rate = 0.0
            # Global default is last resort — only used if the item truly has no rate at all
            if rate == 0.0:
                rate = default_rate

            qty = item_counts.get(str(item.get("Item ID")), 1)
            if rental_type == "Hourly":
                subtotal = rate * duration_hours * qty
            elif rental_type == "Multi-Day":
                subtotal = rate * duration_days * qty
            else:
                subtotal = rate * qty

            total += subtotal
            breakdown.append({
                "item_id": item.get("Item ID"),
                "name": item.get("Name/Description"),
                "qty": qty,
                "rate": rate,
                "subtotal": subtotal,  # converted to display currency below
            })

        # Convert total + each breakdown row from stored currency to display currency.
        # Previously only `total` was converted, leaving breakdown rows showing raw
        # CLP-scale numbers with a "$" prefix (e.g. "$330000.00" next to a $1736 total).
        stored = get_rate_currency()
        display = get_display_currency()

        def _convert(amount):
            if stored == "CLP" and display == "USD":
                return amount / _get_clp_rate()
            if stored == "USD" and display == "CLP":
                return amount * _get_clp_rate()
            return amount

        display_total = _convert(total)
        for b in breakdown:
            b["subtotal"] = round(_convert(b["subtotal"]), 2)

        return jsonify({
            "total": round(display_total, 2),
            "total_raw": round(total, 2),          # in stored currency (CLP)
            "currency": display,
            "breakdown": breakdown,
            "duration_hours": round(duration_hours, 1),
            "duration_days": duration_days,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_view():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_rates":
            db.update_settings({
                "default_hourly_rate":    request.form.get("default_hourly_rate", "").strip(),
                "default_half_day_rate":  request.form.get("default_half_day_rate", "").strip(),
                "default_full_day_rate":  request.form.get("default_full_day_rate", "").strip(),
                "default_multi_day_rate": request.form.get("default_multi_day_rate", "").strip(),
            })
            flash("Default rates saved.", "success")

        elif action == "bulk_rates":
            category = request.form.get("bulk_category", "").strip()
            updates = {
                "Hourly Rate":    request.form.get("bulk_hourly", "").strip(),
                "Half-Day Rate":  request.form.get("bulk_half_day", "").strip(),
                "Full-Day Rate":  request.form.get("bulk_full_day", "").strip(),
                "Multi-Day Rate": request.form.get("bulk_multi_day", "").strip(),
            }
            # Remove empty fields so we don't overwrite with blanks
            updates = {k: v for k, v in updates.items() if v}
            if not updates:
                flash("Enter at least one rate to apply.", "error")
            else:
                inventory = db.get_inventory(force_refresh=True)
                count = 0
                for item in inventory:
                    if not category or item.get("Category") == category:
                        db.update_inventory_item(item["Item ID"], updates)
                        count += 1
                _sync()
                flash(f"Rates applied to {count} item(s).", "success")

        elif action == "change_pin":
            current_pin_input = request.form.get("current_pin", "").strip()
            new_pin = request.form.get("new_pin", "").strip()
            confirm_pin = request.form.get("confirm_pin", "").strip()
            # F-16: read PIN from DB each time — no stale module-level global
            stored_pin = db.get_settings().get("app_pin", "") or os.environ.get("APP_PIN", "1234")
            if not current_pin_input or current_pin_input != stored_pin:
                flash("Current PIN is incorrect.", "error")  # (#8)
            elif not new_pin:
                flash("PIN cannot be empty.", "error")
            elif new_pin != confirm_pin:
                flash("PINs do not match.", "error")
            elif len(new_pin) < 4:
                flash("PIN must be at least 4 characters.", "error")
            else:
                db.update_settings({"app_pin": new_pin})
                flash("PIN updated. Use the new PIN next time you sign in.", "success")

        elif action == "update_general":
            db.update_settings({
                "business_name": request.form.get("business_name", "").strip(),
                "currency":      request.form.get("currency", "USD").strip(),
            })
            flash("General settings saved.", "success")

        elif action == "toggle_pricing":
            new_val = "0" if pricing_enabled() else "1"
            db.update_settings({"pricing_enabled": new_val})
            flash("Pricing tracking " + ("enabled." if new_val == "1" else "disabled. Rates and totals are now hidden — your data is preserved."), "success")

        elif action == "update_sheet_url":
            raw_url = request.form.get("sheet_url", "").strip()
            # Accept full editor URL or bare sheet ID
            match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", raw_url)
            sheet_id = match.group(1) if match else raw_url
            if sheet_id:
                db.update_settings({"sheet_id": sheet_id})
                os.environ["GOOGLE_SHEET_ID"] = sheet_id
                flash("Google Sheet linked. Use 'Push to Google Sheets' to sync your data.", "success")
            else:
                flash("Couldn't find a Sheet ID in that URL. Paste the full editor link.", "error")

        return redirect(url_for("settings_view"))

    settings = db.get_settings()
    inventory = db.get_inventory()
    categories = sorted({i.get("Category", "") for i in inventory if i.get("Category")})
    # Build "last synced X minutes ago" label
    last_synced = settings.get("last_synced", "")
    last_synced_label = "Never"
    if last_synced:
        try:
            ls_dt = _parse_dt(last_synced)
            if ls_dt:
                delta = int((datetime.now() - ls_dt).total_seconds() / 60)
                if delta < 1:
                    last_synced_label = "just now"
                elif delta == 1:
                    last_synced_label = "1 minute ago"
                elif delta < 60:
                    last_synced_label = f"{delta} minutes ago"
                else:
                    last_synced_label = ls_dt.strftime("%b %d, %Y %I:%M %p")
        except Exception:
            last_synced_label = last_synced
    sheet_id = settings.get("sheet_id", "")
    exchange_rate = settings.get("exchange_rate_usd_clp", "950")
    exchange_rate_updated = settings.get("exchange_rate_updated", "")
    return render_template(
        "settings.html",
        settings=settings,
        categories=categories,
        last_synced=last_synced,
        last_synced_label=last_synced_label,
        sheet_id=sheet_id,
        exchange_rate=exchange_rate,
        exchange_rate_updated=exchange_rate_updated,
    )


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    db.clear_cache()
    # If called from a browser form, redirect back with feedback
    referrer = request.referrer or ""
    if "text/html" in request.accept_mimetypes.best or referrer:
        flash("Cache cleared.", "success")
        return redirect(referrer or url_for("settings_view"))
    return jsonify({"status": "ok", "message": "Cache cleared."})


@app.route("/api/sync", methods=["POST"])
@login_required
def api_sync():
    sheet_id = db.get_settings().get("sheet_id", "") or os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        flash("Link a Google Sheet in Settings first.", "error")
        return redirect(url_for("settings_view"))
    try:
        _sheets_client._sheet_id = sheet_id
        result = syncer.push_now(db)
        if result.get("status") == "ok":
            flash(f"Synced to Google Sheets — {result['stats']['inventory_rows']} inventory, {result['stats']['reservation_rows']} reservations.", "success")
        else:
            flash(f"Sync error: {result.get('message', 'unknown error')}", "error")
    except Exception as e:
        flash(f"Sync failed: {e}", "error")
    return redirect(url_for("settings_view"))


@app.route("/api/pull", methods=["POST"])
@login_required
def api_pull():
    sheet_id = db.get_settings().get("sheet_id", "") or os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        flash("Link a Google Sheet in Settings first.", "error")
        return redirect(url_for("settings_view"))
    try:
        _sheets_client._sheet_id = sheet_id
        _sheets_client._spreadsheet = None
        result = syncer.pull_from_sheets(db)
        db.update_settings({"last_synced": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        flash(
            f"Pulled from Google Sheets — {result['inventory_pulled']} inventory rows, "
            f"{result['reservations_pulled']} reservation rows imported.",
            "success",
        )
    except Exception as e:
        msg = str(e)
        if "credentials" in msg.lower() or "not found" in msg.lower():
            flash("Google credentials file not found — see the README for setup instructions.", "error")
        elif "quota" in msg.lower() or "network" in msg.lower() or "connect" in msg.lower():
            flash("Could not reach Google Sheets — check your internet connection and try again.", "error")
        elif "sheet" in msg.lower() or "spreadsheet" in msg.lower():
            flash("Sheet not found — double-check your Google Sheet link in Settings.", "error")
        else:
            flash(f"Pull failed: {e}", "error")
    next_page = request.form.get("next", "settings_view")
    return redirect(url_for(next_page))


# ── Template helpers ──────────────────────────────────────────────────────────

def fmt_datetime(s):
    dt = _parse_dt(str(s or ""))
    if not dt:
        return s or "—"
    return dt.strftime("%b %d, %Y %I:%M %p")

def fmt_date(s):
    dt = _parse_dt(str(s or ""))
    if not dt:
        return s or "—"
    return dt.strftime("%b %d, %Y")

def fmt_currency(v):
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return f"${v}" if v else "—"

def fmt_price(v, currency=None):
    """Format a stored rate in the requested display currency.

    Rates are stored in get_rate_currency() (default CLP).
    Conversion direction depends on stored vs. display currency:
      stored CLP → display USD : divide by exchange rate
      stored CLP → display CLP : show as-is
      stored USD → display CLP : multiply by exchange rate  (legacy fallback)
      stored USD → display USD : show as-is
    """
    try:
        amount = float(v)
    except (TypeError, ValueError):
        return "—"
    if currency is None:
        currency = "USD"
    stored = get_rate_currency()
    if stored == currency:
        if currency == "CLP":
            return f"CLP ${amount:,.0f}"
        return f"${amount:.2f}"
    if stored == "CLP" and currency == "USD":
        return f"${amount / _get_clp_rate():.2f}"
    # stored == "USD" and currency == "CLP"
    return f"CLP ${amount * _get_clp_rate():,.0f}"

app.jinja_env.filters["fmt_datetime"] = fmt_datetime
app.jinja_env.filters["fmt_date"] = fmt_date
app.jinja_env.filters["fmt_currency"] = fmt_currency
app.jinja_env.filters["fmt_price"] = fmt_price
app.jinja_env.globals["now"] = datetime.now
app.jinja_env.globals["app_version"] = APP_VERSION
app.jinja_env.globals["get_display_currency"] = get_display_currency
app.jinja_env.globals["get_rate_currency"] = get_rate_currency
app.jinja_env.globals["get_clp_rate"] = _get_clp_rate
app.jinja_env.globals["pricing_enabled"] = pricing_enabled


@app.route("/api/toggle-currency", methods=["POST"])
@login_required  # (#7)
def toggle_currency():
    # Allow explicit target via form field; fall back to toggle
    target = request.form.get("currency")
    if target in ("USD", "CLP"):
        session["display_currency"] = target
    else:
        current = get_display_currency()
        session["display_currency"] = "CLP" if current == "USD" else "USD"
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/refresh-rate", methods=["POST"])
@login_required
def refresh_rate():
    try:
        rate = _fetch_clp_rate()
        db.update_settings({
            "exchange_rate_usd_clp": str(rate),
            "exchange_rate_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        flash(f"Exchange rate updated: 1 USD = {rate:,.0f} CLP", "success")
    except Exception as e:
        flash(f"Could not fetch rate: {e}", "error")
    return redirect(request.referrer or url_for("settings_view"))


def _oauth_redirect_uri():
    """Return the OAuth callback URI — always localhost so it matches Google's registered URI."""
    port = request.environ.get("SERVER_PORT", "5000")
    return f"http://localhost:{port}/oauth/callback"


@app.route("/oauth/google")
@login_required
def oauth_google_start():
    """Start the Google OAuth2 flow.

    When running as a bundled desktop app the webview is an embedded browser —
    Google blocks OAuth in embedded browsers.  We open the consent URL in the
    user's default system browser instead and return a waiting page that polls
    /api/oauth-status and auto-navigates once the token arrives.
    """
    try:
        from google_auth_oauthlib.flow import Flow
        secrets_path = str(_BASE / "client_secrets.json")
        if not os.path.exists(secrets_path):
            flash(
                "client_secrets.json not found in the app folder. "
                "Please contact support.",
                "error",
            )
            return redirect(url_for("settings_view"))

        # PKCE — required by Google for Desktop/installed apps
        code_verifier = _secrets.token_urlsafe(96)[:128]
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        flow = Flow.from_client_secrets_file(
            secrets_path,
            scopes=SCOPES,
            redirect_uri=_oauth_redirect_uri(),
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",               # always ask so we get a refresh_token
            code_challenge=code_challenge,
            code_challenge_method="S256",
        )
        # Store verifier server-side keyed by state — NOT in session cookie.
        # Protected by lock; expires after 10 min to prevent unbounded growth (F-15).
        _prune_oauth_verifiers()
        with _oauth_verifiers_lock:
            _oauth_verifiers[state] = {"verifier": code_verifier, "expires": time.time() + 600}
        session["oauth_state"] = state

        # Open in the system browser — avoids Google's embedded-browser block.
        # Falls back to a plain redirect if webbrowser.open fails (e.g. running
        # in a terminal during development).
        opened = webbrowser.open(auth_url)
        if opened:
            # F-13: pass auth_url so waiting page can show it as fallback
            return render_template("oauth_waiting.html", auth_url=auth_url)
        # Fallback: plain redirect (dev / non-bundled mode)
        return redirect(auth_url)
    except Exception as e:
        flash(f"Could not start Google sign-in: {e}", "error")
        return redirect(url_for("settings_view"))


@app.route("/oauth/callback")
def oauth_google_callback():
    """Handle Google's redirect after the user grants permission.

    This URL is opened in the system browser (not the webview), so we return a
    self-contained success/error page that the user can close.  The webview's
    oauth_waiting.html page polls /api/oauth-status and navigates back to
    Settings automatically once the token is stored.
    """
    try:
        from google_auth_oauthlib.flow import Flow
        secrets_path = str(_BASE / "client_secrets.json")
        redirect_uri = _oauth_redirect_uri()
        state = request.args.get("state", "")
        with _oauth_verifiers_lock:
            entry = _oauth_verifiers.pop(state, None)
        code_verifier = entry["verifier"] if entry else None
        if code_verifier is None:
            return render_template(
                "oauth_callback_done.html",
                success=False,
                error="Invalid or expired OAuth session. Please try signing in again from the app.",
            )
        flow = Flow.from_client_secrets_file(
            secrets_path,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
            state=state,
        )
        flow.fetch_token(
            authorization_response=request.url,
            code_verifier=code_verifier,
        )
        creds = flow.credentials

        token_json = json.dumps({
            "token":         creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri":     creds.token_uri,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
            "scopes":        list(creds.scopes or SCOPES),
        })
        db.update_settings({"google_oauth_token": token_json})
        # Hot-reload the live client — no restart needed
        _sheets_client.reset_connection(oauth_token_json=token_json)
        session.pop("oauth_state", None)
        return render_template("oauth_callback_done.html", success=True)
    except Exception as e:
        log.error("OAuth callback error: %s", e)
        return render_template("oauth_callback_done.html", success=False, error=str(e))


@app.route("/api/oauth-status")
@login_required
def api_oauth_status():
    """Polling endpoint used by oauth_waiting.html to detect when sign-in completes."""
    settings = db.get_settings()
    connected = len(settings.get("google_oauth_token", "") or "") > 10
    return jsonify({"connected": connected})


@app.route("/oauth/disconnect", methods=["POST"])
@login_required
def oauth_google_disconnect():
    """Remove stored OAuth token and disconnect Google Sheets."""
    db.update_settings({"google_oauth_token": ""})
    _sheets_client.reset_connection(oauth_token_json=None)
    flash("Google account disconnected.", "success")
    return redirect(url_for("settings_view"))


@app.route("/reservations/<res_id>/apply-discount", methods=["POST"])
@login_required
def apply_discount(res_id):
    if not pricing_enabled():
        flash("Pricing tracking is turned off — enable it in Settings to apply discounts.", "error")
        return redirect(url_for("reservation_detail", res_id=res_id))
    try:
        pct = int(request.form.get("discount_pct", "0"))
        if pct not in (5, 10, 15, 20, 25):
            flash("Invalid discount.", "error")
            return redirect(url_for("reservation_detail", res_id=res_id))
        all_res = db.get_reservations(force_refresh=True)
        reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
        if not reservation:
            flash("Reservation not found.", "error")
            return redirect(url_for("reservations"))
        current_amount = float(reservation.get("Payment Amount", "0") or "0")
        if current_amount <= 0:
            flash("No payment amount set to discount.", "error")
            return redirect(url_for("reservation_detail", res_id=res_id))
        discounted = round(current_amount * (1 - pct / 100), 2)
        if discounted <= 0:
            flash("Discount would reduce the total to $0 or less — not applied.", "error")
            return redirect(url_for("reservation_detail", res_id=res_id))
        db.update_reservation(res_id, {
            "Payment Amount": str(discounted),
            "Original Payment Amount": str(current_amount),
            "Discount Percent": str(pct),
        })
        _sync()
        flash(f"{pct}% discount applied — new total: ${discounted:.2f}", "success")
    except Exception as e:
        flash(f"Error applying discount: {e}", "error")
    return redirect(url_for("reservation_detail", res_id=res_id))


# ── Waiver uploads ──────────────────────────────────────────────────────────────

@app.route("/reservations/<res_id>/waiver/upload", methods=["POST"])
@login_required
def upload_waiver(res_id):
    try:
        file = request.files.get("waiver_file")
        if not file or not file.filename:
            flash("No file selected.", "error")
            return redirect(url_for("reservation_detail", res_id=res_id))

        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in _ALLOWED_WAIVER_EXT:
            flash("Waivers must be a PDF, JPG, or PNG file.", "error")
            return redirect(url_for("reservation_detail", res_id=res_id))

        all_res = db.get_reservations(force_refresh=True)
        reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
        if not reservation:
            flash("Reservation not found.", "error")
            return redirect(url_for("reservations"))

        _WAIVERS_DIR.mkdir(parents=True, exist_ok=True)
        # Remove any previous waiver file for this reservation before saving the new one
        old_file = reservation.get("Waiver File", "")
        if old_file:
            old_path = _WAIVERS_DIR / old_file
            if old_path.exists():
                old_path.unlink()

        safe_res_id = secure_filename(res_id)
        filename = f"{safe_res_id}_{_secrets.token_hex(4)}.{ext}"
        file.save(str(_WAIVERS_DIR / filename))

        db.update_reservation(res_id, {
            "Waiver File": filename,
            "Waiver Signed": "Yes",
        })
        _sync()
        flash("Waiver uploaded.", "success")
    except Exception as e:
        flash(f"Error uploading waiver: {e}", "error")
    return redirect(url_for("reservation_detail", res_id=res_id))


@app.route("/reservations/<res_id>/waiver/view")
@login_required
def view_waiver(res_id):
    all_res = db.get_reservations()
    reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
    filename = reservation.get("Waiver File", "") if reservation else ""
    if not filename:
        flash("No waiver on file.", "error")
        return redirect(url_for("reservation_detail", res_id=res_id))
    return send_from_directory(str(_WAIVERS_DIR), filename)


@app.route("/reservations/<res_id>/waiver/remove", methods=["POST"])
@login_required
def remove_waiver(res_id):
    try:
        all_res = db.get_reservations(force_refresh=True)
        reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
        if not reservation:
            flash("Reservation not found.", "error")
            return redirect(url_for("reservations"))
        filename = reservation.get("Waiver File", "")
        if filename:
            path = _WAIVERS_DIR / filename
            if path.exists():
                path.unlink()
        db.update_reservation(res_id, {"Waiver File": ""})
        _sync()
        flash("Waiver removed.", "success")
    except Exception as e:
        flash(f"Error removing waiver: {e}", "error")
    return redirect(url_for("reservation_detail", res_id=res_id))


# ── PDF invoice export ──────────────────────────────────────────────────────────

def _build_invoice_pdf(reservation: dict, reserved_items: list) -> bytes:
    """Render a printable invoice/booking summary PDF for a reservation."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas as pdfcanvas

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=letter)
    width, height = letter
    brand = colors.HexColor("#0f3d4a")
    teal = colors.HexColor("#40C1E8")
    muted = colors.HexColor("#64748b")

    settings = db.get_settings()
    business_name = settings.get("business_name", "").strip() or "Pucon Kayak Retreat"
    show_pricing = pricing_enabled()

    y = height - 0.85 * inch

    # Header
    c.setFillColor(brand)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(0.75 * inch, y, business_name)
    c.setFillColor(teal)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - 0.75 * inch, y, "RENTAL AGREEMENT" if not show_pricing else "INVOICE")
    y -= 0.22 * inch
    c.setFillColor(muted)
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 0.75 * inch, y, f"Reservation: {reservation.get('Reservation ID','')}")
    y -= 0.15 * inch
    c.drawRightString(width - 0.75 * inch, y, f"Date issued: {datetime.now().strftime('%b %d, %Y')}")

    y -= 0.35 * inch
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(0.75 * inch, y, width - 0.75 * inch, y)
    y -= 0.35 * inch

    # Customer block
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.75 * inch, y, "Customer")
    y -= 0.2 * inch
    c.setFont("Helvetica", 10)
    c.drawString(0.75 * inch, y, reservation.get("Customer Name", "—"))
    y -= 0.16 * inch
    contact = " · ".join(filter(None, [reservation.get("Customer Phone", ""), reservation.get("Customer Email", "")]))
    if contact:
        c.setFillColor(muted)
        c.drawString(0.75 * inch, y, contact)
        c.setFillColor(colors.black)
        y -= 0.16 * inch

    y -= 0.15 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.75 * inch, y, "Rental Window")
    y -= 0.2 * inch
    c.setFont("Helvetica", 10)
    c.drawString(0.75 * inch, y, f"Check-out:  {fmt_datetime(reservation.get('Start Date & Time',''))}")
    y -= 0.16 * inch
    c.drawString(0.75 * inch, y, f"Return by:  {fmt_datetime(reservation.get('End Date & Time',''))}")
    y -= 0.16 * inch
    c.drawString(0.75 * inch, y, f"Rental Type:  {reservation.get('Rental Type','—')}")

    y -= 0.4 * inch
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(0.75 * inch, y, width - 0.75 * inch, y)
    y -= 0.3 * inch

    # Equipment table header
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.75 * inch, y, "Equipment")
    y -= 0.25 * inch
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(muted)
    col_id, col_name, col_cat, col_rate = 0.75 * inch, 2.1 * inch, 4.6 * inch, 6.1 * inch
    c.drawString(col_id, y, "ITEM ID")
    c.drawString(col_name, y, "DESCRIPTION")
    c.drawString(col_cat, y, "CATEGORY")
    if show_pricing:
        c.drawString(col_rate, y, "FULL-DAY RATE")
    y -= 0.08 * inch
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(0.75 * inch, y, width - 0.75 * inch, y)
    y -= 0.2 * inch

    c.setFont("Helvetica", 9)
    c.setFillColor(colors.black)
    for item in reserved_items:
        if y < 1.5 * inch:
            c.showPage()
            y = height - 0.85 * inch
        c.drawString(col_id, y, str(item.get("Item ID", ""))[:22])
        c.drawString(col_name, y, str(item.get("Name/Description", ""))[:30])
        c.drawString(col_cat, y, str(item.get("Category", ""))[:18])
        if show_pricing and item.get("Full-Day Rate"):
            c.drawString(col_rate, y, fmt_price(item.get("Full-Day Rate"), get_display_currency()))
        y -= 0.2 * inch

    y -= 0.2 * inch
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(0.75 * inch, y, width - 0.75 * inch, y)
    y -= 0.35 * inch

    # Payment block
    if show_pricing:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(0.75 * inch, y, "Payment")
        y -= 0.22 * inch
        c.setFont("Helvetica", 10)
        c.drawString(0.75 * inch, y, f"Status: {reservation.get('Payment Status','Unpaid')}")
        y -= 0.16 * inch
        if reservation.get("Discount Percent"):
            c.setFillColor(muted)
            c.drawString(0.75 * inch, y, f"Original: {fmt_currency(reservation.get('Original Payment Amount'))}  ·  Discount: {reservation.get('Discount Percent')}%")
            c.setFillColor(colors.black)
            y -= 0.18 * inch
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(brand)
        c.drawString(0.75 * inch, y, f"Total: {fmt_currency(reservation.get('Payment Amount'))}")
        c.setFillColor(colors.black)
        y -= 0.35 * inch
    else:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(0.75 * inch, y, "Waiver")
        y -= 0.22 * inch
        c.setFont("Helvetica", 10)
        signed = "Signed" if reservation.get("Waiver Signed") == "Yes" else "Not signed"
        c.drawString(0.75 * inch, y, signed)
        y -= 0.35 * inch

    # Footer
    c.setFont("Helvetica", 8)
    c.setFillColor(muted)
    c.drawCentredString(width / 2, 0.6 * inch, f"{business_name} — Generated by Pucon Kayak Retreat staff portal")

    c.showPage()
    c.save()
    return buf.getvalue()


@app.route("/reservations/<res_id>/invoice.pdf")
@login_required
def reservation_invoice_pdf(res_id):
    all_res = db.get_reservations()
    reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
    if not reservation:
        flash("Reservation not found.", "error")
        return redirect(url_for("reservations"))
    inventory = db.get_inventory()
    item_ids = _split_item_ids(reservation.get("Item IDs", ""))
    reserved_items = [i for i in inventory if str(i.get("Item ID")) in item_ids]

    try:
        pdf_bytes = _build_invoice_pdf(reservation, reserved_items)
    except Exception as e:
        flash(f"Error generating PDF: {e}", "error")
        return redirect(url_for("reservation_detail", res_id=res_id))

    from flask import Response
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{res_id}_invoice.pdf"'},
    )


# ── Auto-update ───────────────────────────────────────────────────────────────

VERSION_JSON_URL = (
    "https://raw.githubusercontent.com/TSCODEWORK/pucon-kayak-retreat"
    "/master/version.json"
)

# Shared download state — written by background thread, read by /api/update/progress
_update_state: dict = {"status": "idle", "progress": 0, "error": ""}


@app.route("/api/update/check")
@login_required
def api_update_check():
    """Return latest version info from GitHub."""
    try:
        resp = _requests.get(
            VERSION_JSON_URL,
            headers={"User-Agent": "PKR-App/updater", "Cache-Control": "no-cache"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        data["current_version"] = APP_VERSION
        data["update_available"] = data.get("version", "") != APP_VERSION
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "current_version": APP_VERSION}), 500


@app.route("/api/update/download", methods=["POST"])
@login_required
def api_update_download():
    """Download the new DMG in a background thread and track progress."""
    global _update_state
    # C-2: guard against missing/non-JSON body
    data = request.get_json(silent=True) or {}
    download_url = data.get("download_url", "")
    if not download_url:
        return jsonify({"error": "No download URL provided"}), 400
    if _update_state.get("status") == "downloading":
        return jsonify({"status": "already_downloading"}), 200

    _update_state = {"status": "downloading", "progress": 0, "error": "", "path": ""}

    def _do_download():
        global _update_state
        tmp = tempfile.mktemp(suffix=".dmg", prefix="pkr_update_")
        try:
            with _requests.get(
                download_url,
                headers={"User-Agent": "PKR-App/updater"},
                stream=True,
                timeout=120,
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(tmp, "wb") as f:
                    for buf in resp.iter_content(chunk_size=65536):
                        if not buf:
                            continue
                        f.write(buf)
                        downloaded += len(buf)
                        if total:
                            _update_state["progress"] = int(downloaded / total * 100)
            _update_state["status"] = "ready"
            _update_state["progress"] = 100
            _update_state["path"] = tmp
        except Exception as e:
            _update_state["status"] = "error"
            _update_state["error"] = str(e)
            try:
                os.remove(tmp)
            except Exception:
                pass

    threading.Thread(target=_do_download, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/update/progress")
@login_required
def api_update_progress():
    """Poll download progress."""
    return jsonify(_update_state)


@app.route("/api/update/install", methods=["POST"])
@login_required
def api_update_install():
    """Open the downloaded DMG in Finder so the user can drag-install it."""
    if _update_state.get("status") != "ready":
        return jsonify({"error": "No update ready to install"}), 400

    dmg_path = _update_state.get("path", "")
    if not dmg_path or not os.path.exists(dmg_path):
        return jsonify({"error": "DMG file not found — try downloading again"}), 400

    # Just open the DMG — macOS mounts it and shows the Finder window.
    # The user drags the app to Applications themselves; no auto-replace risk.
    subprocess.Popen(
        ["open", dmg_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return jsonify({"status": "opened"})


@app.route("/api/update/quit", methods=["POST"])
@login_required
def api_update_quit():
    """Quit the app so the user can relaunch the newly installed version."""
    def _quit():
        time.sleep(0.8)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_quit, daemon=True).start()
    return jsonify({"status": "quitting"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
