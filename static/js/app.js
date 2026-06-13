// ── Flash auto-dismiss ────────────────────────────────────────────────────────
document.querySelectorAll(".flash").forEach(el => {
  setTimeout(() => el.remove(), 6000);
});

// ── Availability + pricing on new/edit reservation forms ──────────────────────
(function () {
  const form = document.getElementById("reservation-form");
  if (!form) return;

  const startInput   = form.querySelector("[name=start_datetime]");
  const endInput     = form.querySelector("[name=end_datetime]");
  const typeSelect   = form.querySelector("[name=rental_type]");
  const amountInput  = form.querySelector("[name=payment_amount]");
  const conflictBox  = document.getElementById("conflict-warning");
  const conflictText = document.getElementById("conflict-text");
  const pricingBox   = document.getElementById("pricing-breakdown");
  const excludeId    = form.dataset.excludeId || "";
  let debounceTimer  = null;

  function selectedItemIds() {
    return [...form.querySelectorAll("[name=item_ids]:checked")].map(c => c.value);
  }

  function checkAvailabilityAndPricing() {
    const ids   = selectedItemIds();
    const start = startInput?.value;
    const end   = endInput?.value;
    const type  = typeSelect?.value;

    if (!ids.length || !start || !end || start >= end) {
      if (conflictBox) conflictBox.classList.remove("visible");
      if (pricingBox)  pricingBox.innerHTML = "";
      return;
    }

    const params = new URLSearchParams({ items: ids.join(","), start, end, exclude: excludeId });

    // Availability check
    fetch("/api/availability?" + params)
      .then(r => r.json())
      .then(data => {
        if (data.conflicts && data.conflicts.length > 0) {
          const names = data.conflicts.map(c => `${c.customer} (${c.reservation_id})`).join(", ");
          if (conflictText) conflictText.textContent = "Conflict with: " + names;
          if (conflictBox)  conflictBox.classList.add("visible");
        } else {
          if (conflictBox) conflictBox.classList.remove("visible");
        }
      })
      .catch(() => {});

    // Pricing
    if (!type || !pricingBox) return;
    const pParams = new URLSearchParams({ items: ids.join(","), start, end, type });
    fetch("/api/pricing?" + pParams)
      .then(r => r.json())
      .then(data => {
        if (data.error) { pricingBox.innerHTML = ""; return; }
        let html = `<div class="pricing-box">
          <h4>Suggested Price</h4>`;
        data.breakdown.forEach(b => {
          html += `<div class="pricing-row"><span>${b.name}</span><span>$${b.subtotal.toFixed(2)}</span></div>`;
        });
        let dur = "";
        if (data.duration_hours) {
          dur = type === "Multi-Day"
            ? ` · ${data.duration_days} day${data.duration_days !== 1 ? "s" : ""}`
            : ` · ${data.duration_hours}h`;
        }
        html += `<div class="pricing-row pricing-total"><span>Total${dur}</span><span>$${data.total.toFixed(2)}</span></div></div>`;
        pricingBox.innerHTML = html;
        // Pre-fill amount if empty or zero
        if (amountInput && (!amountInput.value || parseFloat(amountInput.value) === 0)) {
          amountInput.value = data.total.toFixed(2);
        }
      })
      .catch(() => { pricingBox.innerHTML = ""; });
  }

  function scheduleCheck() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(checkAvailabilityAndPricing, 400);
  }

  form.addEventListener("change", scheduleCheck);
  if (startInput) startInput.addEventListener("input", scheduleCheck);
  if (endInput)   endInput.addEventListener("input", scheduleCheck);
  if (typeSelect) typeSelect.addEventListener("change", scheduleCheck);
})();

// ── Inventory status quick-update modal ───────────────────────────────────────
(function () {
  const modal    = document.getElementById("inv-modal");
  const overlay  = document.getElementById("inv-overlay");
  if (!modal) return;

  window.openInvModal = function (itemId, currentStatus, currentNotes) {
    document.getElementById("inv-modal-id").textContent    = itemId;
    document.getElementById("inv-form-id").value           = itemId;
    document.getElementById("inv-status-select").value     = currentStatus;
    document.getElementById("inv-notes-input").value       = currentNotes;
    document.getElementById("inv-modal-action").action     = `/inventory/${encodeURIComponent(itemId)}/update`;
    modal.classList.remove("hidden");
    overlay.classList.remove("hidden");
  };

  window.closeInvModal = function () {
    modal.classList.add("hidden");
    overlay.classList.add("hidden");
  };

  overlay.addEventListener("click", closeInvModal);
})();

// ── Return modal ──────────────────────────────────────────────────────────────
(function () {
  const modal   = document.getElementById("return-modal");
  const overlay = document.getElementById("return-overlay");
  if (!modal) return;

  window.openReturnModal = function () {
    modal.classList.remove("hidden");
    overlay.classList.remove("hidden");
  };
  window.closeReturnModal = function () {
    modal.classList.add("hidden");
    overlay.classList.add("hidden");
  };
  overlay.addEventListener("click", closeReturnModal);
})();

// ── Calendar ──────────────────────────────────────────────────────────────────
(function () {
  const calRoot = document.getElementById("calendar-root");
  if (!calRoot) return;

  const reservations = JSON.parse(document.getElementById("cal-data").textContent || "[]");
  let currentYear, currentMonth;

  function today() {
    const d = new Date();
    return { year: d.getFullYear(), month: d.getMonth() };
  }

  function parseDt(s) {
    if (!s) return null;
    const d = new Date(s.replace(" ", "T"));
    return isNaN(d) ? null : d;
  }

  function dateKey(y, m, d) {
    return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }

  function buildEventMap() {
    const map = {};
    reservations.forEach(r => {
      const start = parseDt(r.start);
      const end   = parseDt(r.end);
      if (!start || !end) return;
      const cur = new Date(start);
      cur.setHours(0, 0, 0, 0);
      const endDay = new Date(end);
      endDay.setHours(0, 0, 0, 0);
      while (cur <= endDay) {
        const key = dateKey(cur.getFullYear(), cur.getMonth(), cur.getDate());
        if (!map[key]) map[key] = [];
        map[key].push(r);
        cur.setDate(cur.getDate() + 1);
      }
    });
    return map;
  }

  function render(year, month) {
    currentYear  = year;
    currentMonth = month;
    const eventMap = buildEventMap();
    const monthNames = ["January","February","March","April","May","June","July","August","September","October","November","December"];
    const dayNames   = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];

    const todayObj = new Date();
    const firstDay = new Date(year, month, 1);
    const lastDay  = new Date(year, month + 1, 0);
    const startPad = firstDay.getDay();

    let html = `
      <div class="calendar-nav">
        <button class="btn btn-ghost btn-sm" onclick="calNav(-1)">← Prev</button>
        <h2>${monthNames[month]} ${year}</h2>
        <button class="btn btn-ghost btn-sm" onclick="calNav(1)">Next →</button>
        <button class="btn btn-ghost btn-sm" onclick="calGoToday()">Today</button>
      </div>
      <div class="cal-grid">
        ${dayNames.map(d => `<div class="cal-header">${d}</div>`).join("")}`;

    // padding cells
    for (let i = 0; i < startPad; i++) {
      const prevDate = new Date(year, month, -startPad + i + 1);
      html += `<div class="cal-day other-month"><div class="cal-day-num">${prevDate.getDate()}</div></div>`;
    }

    for (let d = 1; d <= lastDay.getDate(); d++) {
      const key     = dateKey(year, month, d);
      const events  = eventMap[key] || [];
      const isToday = d === todayObj.getDate() && month === todayObj.getMonth() && year === todayObj.getFullYear();

      html += `<div class="cal-day${isToday ? " today" : ""}" onclick="calSelectDay('${key}', ${d})">
        <div class="cal-day-num">${d}</div>`;

      events.slice(0, 3).forEach(ev => {
        const cls = ev.status === "Checked Out" ? "checked-out" : ev.status === "Returned" ? "returned" : "upcoming";
        html += `<div class="cal-event ${cls}" title="${ev.customer}">${ev.customer}</div>`;
      });
      if (events.length > 3) html += `<div class="cal-event upcoming">+${events.length - 3} more</div>`;

      html += `</div>`;
    }

    // trailing padding
    const totalCells = startPad + lastDay.getDate();
    const remainder  = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
    for (let i = 1; i <= remainder; i++) {
      html += `<div class="cal-day other-month"><div class="cal-day-num">${i}</div></div>`;
    }

    html += `</div>`;
    calRoot.innerHTML = html;
  }

  window.calNav = function (dir) {
    let m = currentMonth + dir;
    let y = currentYear;
    if (m > 11) { m = 0;  y++; }
    if (m < 0)  { m = 11; y--; }
    render(y, m);
    document.getElementById("cal-detail").innerHTML = "";
  };

  window.calGoToday = function () {
    const t = today();
    render(t.year, t.month);
  };

  window.calSelectDay = function (key, dayNum) {
    const events = reservations.filter(r => {
      const start = parseDt(r.start);
      const end   = parseDt(r.end);
      if (!start || !end) return false;
      const startKey = dateKey(start.getFullYear(), start.getMonth(), start.getDate());
      const endKey   = dateKey(end.getFullYear(),   end.getMonth(),   end.getDate());
      return key >= startKey && key <= endKey;
    });

    const panel = document.getElementById("cal-detail");

    const fmt = s => { const d = parseDt(s); return d ? d.toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : s; };

    let html = `<div class="cal-detail-panel">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.75rem;flex-wrap:wrap;gap:.5rem">
        <h3>${events.length ? events.length + ` reservation${events.length !== 1 ? "s" : ""} · ${key}` : key}</h3>
        <a href="/reservations/new?start=${key}" class="btn btn-primary btn-sm">+ New Booking</a>
      </div>`;
    if (!events.length) {
      html += `<div class="text-muted text-sm">No reservations on this day.</div>`;
    }
    events.forEach(ev => {
      html += `<div class="today-item" style="margin-bottom:.5rem">
        <div>
          <div class="today-name"><a href="/reservations/${ev.id}">${ev.customer}</a></div>
          <div class="today-meta">${ev.items || ""} · ${ev.rental_type || ""}</div>
          <div class="today-meta">${fmt(ev.start)} → ${fmt(ev.end)}</div>
        </div>
        <span class="badge badge-${(ev.status || "").toLowerCase().replace(" ","-")}">${ev.status}</span>
      </div>`;
    });
    html += `</div>`;
    panel.innerHTML = html;
  };

  const t = today();
  render(t.year, t.month);
})();
