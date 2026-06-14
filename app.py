import os
import re
import sys
import uuid
import json
import math
import logging
import calendar as cal_module
import functools
import threading
import time
import urllib.request
from pathlib import Path
from datetime import datetime, date, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify,
)
from dotenv import load_dotenv
from utils import _parse_dt  # single source of truth
from db import DatabaseClient, DatabaseError
from sheets import SheetsClient, SheetsError
from sync import SheetsSyncer

log = logging.getLogger(__name__)

# ── Resolve base directory (handles PyInstaller bundle) ───────────────────────
# main.py sets PKR_BASE_DIR before importing this module when running bundled.
# Fallback: directory of this file (normal run).
_BASE = Path(os.environ.get("PKR_BASE_DIR", Path(__file__).parent))

# Load .env — main.py loads it first (from App Support dir), so this is a no-op
# when running bundled; it loads .env from CWD when running directly.
load_dotenv()

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

# Load PIN from DB (falls back to env var, then "1234")
_startup_settings = db.get_settings()
_saved_pin = _startup_settings.get("app_pin", "")
APP_PIN = _saved_pin or os.environ.get("APP_PIN", "1234")

# Load sheet ID from DB settings (overrides env var so it survives restarts)
_saved_sheet_id = _startup_settings.get("sheet_id", "")
if _saved_sheet_id:
    os.environ["GOOGLE_SHEET_ID"] = _saved_sheet_id

# Sheets sync is optional — silently disabled if credentials/sheet not configured
_sheets_client = SheetsClient(
    credentials_file=os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
    sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""),
)
syncer = SheetsSyncer(_sheets_client)


_last_sync_sheet_id = ""


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
        if request.form.get("pin") == APP_PIN:
            session["authenticated"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("dashboard")
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
    view = request.args.get("view", "day")
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

    except SheetsError as e:
        sheets_error = str(e)

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
        # month view
        month_stats=month_stats,
        month_days=month_days,
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
            ql = q.lower()
            filtered = [
                r for r in filtered
                if ql in r.get("Customer Name", "").lower()
                or ql in r.get("Customer Email", "").lower()
                or ql in r.get("Customer Phone", "").lower()
                or ql in r.get("Reservation ID", "").lower()
                or ql in r.get("Item IDs", "").lower()
            ]
        if status_filter:
            filtered = [r for r in filtered if r.get("Reservation Status") == status_filter]
        if date_filter:
            filtered = [
                r for r in filtered
                if str(r.get("Start Date & Time", "")).startswith(date_filter)
                or str(r.get("End Date & Time", "")).startswith(date_filter)
            ]

        filtered.sort(key=lambda r: r.get("Start Date & Time", ""), reverse=True)

    except SheetsError as e:
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

def _validate_reservation_form(form):
    """Validate the fields common to both 'create' and 'edit' reservation forms.

    Returns (errors, parsed) where:
      errors  — list of human-readable error strings (empty = valid)
      parsed  — dict with start_str, end_str, start_dt, end_dt, pay_amt
    """
    errors = []
    start_str = form.get("start_datetime", "").strip()
    end_str   = form.get("end_datetime",   "").strip()

    if not form.getlist("item_ids"):
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
                conflicts = db.check_conflicts(item_ids, start_str, end_str)
                if conflicts:
                    names = ", ".join(c.get("Customer Name", "?") for c in conflicts)
                    errors.append(
                        f"Booking conflict: one or more items are already reserved by {names} during this time."
                    )
            except SheetsError as e:
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
            return redirect(url_for("reservations"))
        except SheetsError as e:
            flash(f"Error saving reservation: {e}", "error")

    return render_template("new_reservation.html", inventory=inventory, form_data=form_data,
                           item_ids=[], active_reservations=active_reservations)


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
                item_ids = [i.strip() for i in str(reservation.get("Item IDs", "")).split(",") if i.strip()]

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
                item_ids = request.form.getlist("item_ids")
                edit_errors, parsed = _validate_reservation_form(request.form)
                start_str = parsed["start_str"]
                end_str   = parsed["end_str"]
                pay_amt   = parsed["pay_amt"]
                if edit_errors:
                    for err in edit_errors:
                        flash(err, "error")
                    return redirect(url_for("reservation_detail", res_id=res_id))

                conflicts = db.check_conflicts(item_ids, start_str, end_str, exclude_id=res_id)
                if conflicts:
                    names = ", ".join(c.get("Customer Name", "?") for c in conflicts)
                    flash(f"Booking conflict with {names}.", "error")
                    return redirect(url_for("reservation_detail", res_id=res_id))

                updates = {
                    "Customer Name": request.form.get("customer_name", "").strip(),
                    "Customer Phone": request.form.get("customer_phone", "").strip(),
                    "Customer Email": request.form.get("customer_email", "").strip(),
                    "Item IDs": ", ".join(item_ids),
                    "Start Date & Time": start_str,
                    "End Date & Time": end_str,
                    "Rental Type": request.form.get("rental_type", ""),
                    "Payment Status": request.form.get("payment_status", "Unpaid"),
                    "Payment Amount": str(pay_amt),
                    "Waiver Signed": "Yes" if request.form.get("waiver_signed") else "No",
                    "Notes": request.form.get("notes", "").strip(),
                }
                db.update_reservation(res_id, updates)
                _sync()
                flash("Reservation updated.", "success")

            return redirect(url_for("reservation_detail", res_id=res_id))

        # Reload after any updates — guard against None if a Sheets pull raced us (#1)
        all_res = db.get_reservations(force_refresh=True)
        reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
        if not reservation:
            flash("Reservation no longer found — it may have been removed.", "error")
            return redirect(url_for("reservations"))
        item_ids = [i.strip() for i in str(reservation.get("Item IDs", "")).split(",") if i.strip()]
        reserved_items = [i for i in inventory if str(i.get("Item ID")) in item_ids]

        # Discount calculation (kayak rentals only, 10+ days)
        discount_pct = 0
        discount_days = 0
        has_kayak_items = any(
            i.get("Category", "") in KAYAK_CATEGORIES for i in reserved_items
        )
        if has_kayak_items:
            start_dt_d = _parse_dt(reservation.get("Start Date & Time", ""))
            end_dt_d = _parse_dt(reservation.get("End Date & Time", ""))
            if start_dt_d and end_dt_d:
                discount_days = (end_dt_d - start_dt_d).days
                if discount_days >= 10:
                    if discount_days <= 15:   discount_pct = 5
                    elif discount_days <= 20: discount_pct = 10
                    elif discount_days <= 25: discount_pct = 15
                    elif discount_days <= 30: discount_pct = 20
                    else:                     discount_pct = 25

        return render_template(
            "reservation_detail.html",
            reservation=reservation,
            inventory=inventory,
            reserved_items=reserved_items,
            item_ids=item_ids,
            discount_pct=discount_pct,
            discount_days=discount_days,
            has_kayak_items=has_kayak_items,
        )

    except SheetsError as e:
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

    except SheetsError as e:
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


@app.route("/inventory/<item_id>/delete", methods=["POST"])
@login_required
def delete_inventory(item_id):
    try:
        # Guard: block deletion if item is referenced in any active reservation
        active_statuses = {"Upcoming", "Checked Out"}
        active_refs = [
            r for r in db.get_reservations()
            if r.get("Reservation Status") in active_statuses
            and item_id in [i.strip() for i in str(r.get("Item IDs", "")).split(",") if i.strip()]
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


@app.route("/inventory/<item_id>/update", methods=["POST"])
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
        if quantity:
            updates["Quantity"] = quantity
        # Per-item rates — allow blank to clear a rate
        for field, col in [
            ("hourly_rate",    "Hourly Rate"),
            ("half_day_rate",  "Half-Day Rate"),
            ("full_day_rate",  "Full-Day Rate"),
            ("multi_day_rate", "Multi-Day Rate"),
        ]:
            if field in request.form:
                updates[col] = request.form.get(field, "").strip()
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
    except SheetsError as e:
        sheets_error = str(e)

    return render_template("calendar.html", cal_events=events, sheets_error=sheets_error)


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/availability")
@login_required
def api_availability():
    try:
        raw = request.args.get("items", "")
        item_ids = [i.strip() for i in raw.split(",") if i.strip()]
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
        item_ids = [i.strip() for i in raw.split(",") if i.strip()]
        rental_type = request.args.get("type", "Hourly")
        start_str = request.args.get("start", "")
        end_str = request.args.get("end", "")

        inventory = db.get_inventory()
        items = [i for i in inventory if str(i.get("Item ID")) in item_ids]

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
            # Fall back to settings default if the item has no individual rate set
            if rate == 0.0:
                rate = default_rate

            if rental_type == "Hourly":
                subtotal = rate * duration_hours
            elif rental_type == "Multi-Day":
                subtotal = rate * duration_days
            else:
                subtotal = rate

            total += subtotal
            breakdown.append({
                "item_id": item.get("Item ID"),
                "name": item.get("Name/Description"),
                "rate": rate,
                "subtotal": round(subtotal, 2),
            })

        return jsonify({
            "total": round(total, 2),
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
            global APP_PIN
            current_pin_input = request.form.get("current_pin", "").strip()
            new_pin = request.form.get("new_pin", "").strip()
            confirm_pin = request.form.get("confirm_pin", "").strip()
            if not current_pin_input or current_pin_input != APP_PIN:
                flash("Current PIN is incorrect.", "error")  # (#8)
            elif not new_pin:
                flash("PIN cannot be empty.", "error")
            elif new_pin != confirm_pin:
                flash("PINs do not match.", "error")
            elif len(new_pin) < 4:
                flash("PIN must be at least 4 characters.", "error")
            else:
                db.update_settings({"app_pin": new_pin})
                APP_PIN = new_pin
                flash("PIN updated. Use the new PIN next time you sign in.", "success")

        elif action == "update_general":
            db.update_settings({
                "business_name": request.form.get("business_name", "").strip(),
                "currency":      request.form.get("currency", "USD").strip(),
            })
            flash("General settings saved.", "success")

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
    return redirect(url_for("settings_view"))


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
    """Format a price in the current display currency (USD or CLP)."""
    try:
        amount = float(v)
    except (TypeError, ValueError):
        return "—"
    if currency is None:
        currency = "USD"  # default fallback
    if currency == "CLP":
        clp = amount * _get_clp_rate()
        return f"CLP ${clp:,.0f}"
    else:
        return f"${amount:.2f}"

app.jinja_env.filters["fmt_datetime"] = fmt_datetime
app.jinja_env.filters["fmt_date"] = fmt_date
app.jinja_env.filters["fmt_currency"] = fmt_currency
app.jinja_env.filters["fmt_price"] = fmt_price
app.jinja_env.globals["now"] = datetime.now
app.jinja_env.globals["get_display_currency"] = get_display_currency
app.jinja_env.globals["get_clp_rate"] = _get_clp_rate


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


@app.route("/reservations/<res_id>/apply-discount", methods=["POST"])
@login_required
def apply_discount(res_id):
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
        notes_existing = reservation.get("Notes", "")
        discount_note = f"\n[{pct}% extended rental discount applied — original amount: ${current_amount:.2f}]"
        db.update_reservation(res_id, {
            "Payment Amount": str(discounted),
            "Notes": (notes_existing + discount_note).strip(),
        })
        _sync()
        flash(f"{pct}% discount applied — new total: ${discounted:.2f}", "success")
    except Exception as e:
        flash(f"Error applying discount: {e}", "error")
    return redirect(url_for("reservation_detail", res_id=res_id))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
