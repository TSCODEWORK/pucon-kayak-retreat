import os
import sys
import uuid
import json
import functools
from pathlib import Path
from datetime import datetime, date
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify,
)
from dotenv import load_dotenv
from db import DatabaseClient, DatabaseError, _parse_dt
from sheets import SheetsClient
from sync import SheetsSyncer

# Treat DatabaseError the same as SheetsError throughout
SheetsError = DatabaseError

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


def _sync():
    """Push to Sheets in background after every write."""
    sheet_id = db.get_settings().get("sheet_id", "") or os.environ.get("GOOGLE_SHEET_ID", "")
    if sheet_id:
        # Keep the client's sheet_id current in case it was set after startup
        _sheets_client._sheet_id = sheet_id
        _sheets_client._spreadsheet = None  # force reconnect with new ID
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
    sheets_error = None
    today_pickups = []
    today_returns = []
    overdue = []
    total_items = rented_items = available_items = maintenance_items = 0

    try:
        reservations = db.get_reservations()
        inventory = db.get_inventory()
        today = date.today()

        for r in reservations:
            status = r.get("Reservation Status", "")
            if status in ("Canceled", "Returned"):
                continue
            start = _parse_dt(r.get("Start Date & Time", ""))
            end = _parse_dt(r.get("End Date & Time", ""))

            if start and start.date() == today and status == "Upcoming":
                today_pickups.append(r)
            if end:
                if end.date() == today and status == "Checked Out":
                    today_returns.append(r)
                elif end.date() < today and status == "Checked Out":
                    overdue.append(r)

        total_items = len(inventory)
        rented_items = sum(1 for i in inventory if i.get("Status") == "Rented")
        available_items = sum(1 for i in inventory if i.get("Status") == "Available")
        maintenance_items = sum(1 for i in inventory if i.get("Status") == "Maintenance")

    except SheetsError as e:
        sheets_error = str(e)

    return render_template(
        "dashboard.html",
        today=date.today(),
        today_pickups=today_pickups,
        today_returns=today_returns,
        overdue=overdue,
        total_items=total_items,
        rented_items=rented_items,
        available_items=available_items,
        maintenance_items=maintenance_items,
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
    reservations_json = json.dumps([
        {
            "start": r.get("Start Date & Time", ""),
            "end":   r.get("End Date & Time", ""),
            "status": r.get("Reservation Status", ""),
            "items":  r.get("Item IDs", ""),
        }
        for r in reservations_raw
        if r.get("Reservation Status") not in ("Canceled", "Returned")
    ])

    form_data = {}

    if request.method == "POST":
        form_data = request.form.to_dict()
        item_ids = request.form.getlist("item_ids")
        start_str = request.form.get("start_datetime", "").strip()
        end_str = request.form.get("end_datetime", "").strip()

        errors = []
        if not request.form.get("customer_name", "").strip():
            errors.append("Customer name is required.")
        if not item_ids:
            errors.append("Select at least one item.")
        if not start_str:
            errors.append("Start date/time is required.")
        if not end_str:
            errors.append("End date/time is required.")
        if start_str and end_str and start_str >= end_str:
            errors.append("End time must be after start time.")

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
            return render_template("new_reservation.html", inventory=inventory, form_data=form_data, item_ids=item_ids)

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
                           item_ids=[], reservations_json=reservations_json)


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
                    for iid in item_ids:
                        item = next((i for i in inventory if str(i.get("Item ID")) == iid), None)
                        if item and item.get("Status") == "Rented":
                            db.update_inventory_item(iid, {"Status": "Available"})

                db.update_reservation(res_id, updates)
                _sync()
                flash(f"Status updated to {new_status}.", "success")

            elif action == "update_details":
                item_ids = request.form.getlist("item_ids")
                start_str = request.form.get("start_datetime", "").strip()
                end_str = request.form.get("end_datetime", "").strip()

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
                    "Payment Amount": request.form.get("payment_amount", "0"),
                    "Waiver Signed": "Yes" if request.form.get("waiver_signed") else "No",
                    "Notes": request.form.get("notes", "").strip(),
                }
                db.update_reservation(res_id, updates)
                _sync()
                flash("Reservation updated.", "success")

            return redirect(url_for("reservation_detail", res_id=res_id))

        # Reload after any updates
        all_res = db.get_reservations(force_refresh=True)
        reservation = next((r for r in all_res if str(r.get("Reservation ID")) == res_id), None)
        item_ids = [i.strip() for i in str(reservation.get("Item IDs", "")).split(",") if i.strip()]
        reserved_items = [i for i in inventory if str(i.get("Item ID")) in item_ids]

        return render_template(
            "reservation_detail.html",
            reservation=reservation,
            inventory=inventory,
            reserved_items=reserved_items,
            item_ids=item_ids,
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

    return render_template(
        "inventory.html",
        items=items,
        categories=categories,
        category_filter=category_filter,
        status_filter=status_filter,
        sheets_error=sheets_error,
    )


@app.route("/inventory/add", methods=["POST"])
@login_required
def add_equipment():
    try:
        item_id = request.form.get("item_id", "").strip()
        if not item_id:
            flash("Item ID is required.", "error")
            return redirect(url_for("inventory_view"))

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
        condition_notes = request.form.get("condition_notes")
        if status:
            updates["Status"] = status
        if condition_notes is not None:
            updates["Condition Notes"] = condition_notes
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
    res_json = "[]"
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
        res_json = json.dumps(events)
    except SheetsError as e:
        sheets_error = str(e)

    return render_template("calendar.html", reservations_json=res_json, sheets_error=sheets_error)


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
            duration_days = max(1, round(duration_hours / 24))

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
            new_pin = request.form.get("new_pin", "").strip()
            confirm_pin = request.form.get("confirm_pin", "").strip()
            if not new_pin:
                flash("PIN cannot be empty.", "error")
            elif new_pin != confirm_pin:
                flash("PINs do not match.", "error")
            elif len(new_pin) < 4:
                flash("PIN must be at least 4 characters.", "error")
            else:
                db.update_settings({"app_pin": new_pin})
                global APP_PIN
                APP_PIN = new_pin
                flash("PIN updated. Use the new PIN next time you sign in.", "success")

        elif action == "update_general":
            db.update_settings({
                "business_name": request.form.get("business_name", "").strip(),
                "currency":      request.form.get("currency", "USD").strip(),
            })
            flash("General settings saved.", "success")

        elif action == "update_sheet_url":
            import re
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
    return render_template("settings.html", settings=settings, categories=categories)


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    db.clear_cache()
    return jsonify({"status": "ok", "message": "Cache cleared."})


@app.route("/api/sync", methods=["POST"])
@login_required
def api_sync():
    if not os.environ.get("GOOGLE_SHEET_ID"):
        return jsonify({"status": "skipped", "message": "No Google Sheet configured."})
    result = syncer.push_now(db)
    return jsonify(result)


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

app.jinja_env.filters["fmt_datetime"] = fmt_datetime
app.jinja_env.filters["fmt_date"] = fmt_date
app.jinja_env.globals["now"] = datetime.now


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
