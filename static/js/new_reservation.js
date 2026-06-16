// ── Data ───────────────────────────────────────────────────────────────────
const INVENTORY    = JSON.parse(document.getElementById('inv-data').textContent);
const RESERVATIONS = JSON.parse(document.getElementById('res-data').textContent);

// ── State ──────────────────────────────────────────────────────────────────
let drpStart = null, drpEnd = null, drpHover = null;
let drpBaseYear = new Date().getFullYear(), drpBaseMonth = new Date().getMonth();
// selectedItems: Map<itemId, quantitySelected> — supports booking more than
// one unit of the same Item ID when its inventory Quantity allows it.
let selectedItems = new Map();
function totalSelectedUnits() {
  let n = 0;
  for (const v of selectedItems.values()) n += v;
  return n;
}

// ── Constants ──────────────────────────────────────────────────────────────
const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December'];
const TODAY = (() => { const d = new Date(); d.setHours(0,0,0,0); return d; })();

// ── Helpers ────────────────────────────────────────────────────────────────
function dateKey(d) { return d.toISOString().slice(0,10); }
function parseDate(s) { const [y,m,d] = s.split('-').map(Number); return new Date(y,m-1,d); }
function fmtDate(d)  { return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}); }
function fmtShort(d) { return d.toLocaleDateString('en-US',{month:'short',day:'numeric'}); }

// ── URL pre-fill ───────────────────────────────────────────────────────────
(function() {
  const p = new URLSearchParams(location.search);
  const s = p.get('start');
  if (s) { drpStart = parseDate(s); drpBaseMonth = drpStart.getMonth(); drpBaseYear = drpStart.getFullYear(); }
  const e = p.get('end');
  if (e) { drpEnd = parseDate(e); }
})();

// ── Busy days from existing reservations ───────────────────────────────────
function buildBusyDays() {
  const busy = new Set();
  for (const r of RESERVATIONS) {
    if (!r.start || !r.end) continue;
    let d = new Date(r.start.replace(' ','T')); d.setHours(0,0,0,0);
    const e = new Date(r.end.replace(' ','T'));  e.setHours(0,0,0,0);
    while (d <= e) { busy.add(dateKey(d)); d = new Date(d.getTime()+86400000); }
  }
  return busy;
}
const busyDays = buildBusyDays();

// Returns Map<itemId, unitsAlreadyBooked> for reservations overlapping [start,end).
// Repeated IDs in a reservation's item list represent multiple booked units.
function getBookedItems(start, end) {
  const booked = new Map();
  for (const r of RESERVATIONS) {
    if (!r.start || !r.end) continue;
    const rs = new Date(r.start.replace(' ','T'));
    const re = new Date(r.end.replace(' ','T'));
    if (start < re && end > rs) {
      for (const id of (r.items||'').split(',').map(s=>s.trim()).filter(Boolean)) {
        booked.set(id, (booked.get(id) || 0) + 1);
      }
    }
  }
  return booked;
}

// ── Date Range Picker ──────────────────────────────────────────────────────
function drpShift(dir) {
  drpBaseMonth += dir;
  if (drpBaseMonth > 11) { drpBaseMonth = 0; drpBaseYear++; }
  if (drpBaseMonth < 0)  { drpBaseMonth = 11; drpBaseYear--; }
  renderDRP();
}

function drpClear() {
  drpStart = drpEnd = drpHover = null;
  renderDRP();
}

function tryGoStep2() {
  if (!drpStart) {
    showDateError('Please click a start date on the calendar above.');
    return;
  }
  if (!drpEnd) {
    showDateError('Please click a return date to complete your selection.');
    return;
  }
  // Same-day booking: ensure pickup time is before return time (#22)
  if (drpStart.getTime() === drpEnd.getTime()) {
    const st = document.getElementById('drp-start-time').value || '09:00';
    const et = document.getElementById('drp-end-time').value || '17:00';
    if (st >= et) {
      showDateError('For same-day rentals, pickup time must be before the return time.');
      return;
    }
  }
  clearDateError();
  goStep(2);
}

function showDateError(msg) {
  let el = document.getElementById('drp-error');
  if (!el) {
    el = document.createElement('div');
    el.id = 'drp-error';
    el.style.cssText = 'color:var(--red-600);font-weight:600;font-size:.875rem;margin-top:.5rem;padding:.5rem .75rem;background:#fef2f2;border-radius:6px;border:1px solid #fecaca';
    document.getElementById('drp-selection').after(el);
  }
  el.textContent = '⚠ ' + msg;
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function clearDateError() {
  document.getElementById('drp-error')?.remove();
}

function drpClickDay(key) {
  const d = parseDate(key);
  if (d < TODAY) return; // ignore past dates
  if (!drpStart || (drpStart && drpEnd)) {
    drpStart = d; drpEnd = null;
  } else {
    if (d < drpStart) { drpEnd = drpStart; drpStart = d; }
    else { drpEnd = d; }
  }
  drpHover = null;
  clearDateError();
  renderDRP();
  updateHiddenFields();
}

function drpHoverDay(key) {
  // Hover re-render removed — it destroyed day-cell DOM nodes between mousedown and
  // mouseup, preventing the click event from firing on the end-date selection.
  // CSS :hover on .drp-day handles the visual feedback without touching the DOM.
}

function renderMonth(idx, year, month) {
  const firstDow = new Date(year, month, 1).getDay();
  const days     = new Date(year, month+1, 0).getDate();
  const hi = drpEnd || drpHover;
  const lo = drpStart;
  document.getElementById(`drp-title-${idx}`).textContent = `${MONTHS[month]} ${year}`;
  let html = '';
  for (let i = 0; i < firstDow; i++) html += '<div class="drp-day drp-empty"></div>';
  for (let d = 1; d <= days; d++) {
    const dt  = new Date(year, month, d);
    const key = dateKey(dt);
    const isPast = dt < TODAY;
    let cls   = 'drp-day';
    if (isPast) cls += ' drp-past';
    if (dt.getTime() === TODAY.getTime()) cls += ' drp-today';
    if (busyDays.has(key)) cls += ' drp-busy';
    if (lo && hi) {
      const a = lo<hi?lo:hi, b = lo<hi?hi:lo;
      if (dt>a && dt<b) cls += ' drp-in-range';
      if (dt.getTime()===a.getTime()) cls += ' drp-range-start';
      if (dt.getTime()===b.getTime()) cls += ' drp-range-end';
    } else if (lo && dt.getTime()===lo.getTime()) cls += ' drp-range-start drp-range-end';
    // data-date used by delegated listeners — no inline onclick/onmouseenter
    html += `<div class="${cls}" data-date="${key}">${d}${busyDays.has(key)?'<span class="drp-dot"></span>':''}</div>`;
  }
  document.getElementById(`drp-grid-${idx}`).innerHTML = html;
}

// Attach delegated listeners once — survive re-renders because the grid containers stay
function initDRPListeners() {
  for (let i = 0; i < 2; i++) {
    const grid = document.getElementById(`drp-grid-${i}`);
    // touchstart fires immediately on mobile (no 300ms delay)
    grid.addEventListener('touchstart', e => {
      const day = e.target.closest('[data-date]');
      if (day) { e.preventDefault(); drpClickDay(day.dataset.date); }
    }, { passive: false });
    grid.addEventListener('click', e => {
      const day = e.target.closest('[data-date]');
      if (day) drpClickDay(day.dataset.date);
    });
    grid.addEventListener('mouseover', e => {
      const day = e.target.closest('[data-date]');
      drpHoverDay(day ? day.dataset.date : null);
    });
    grid.addEventListener('mouseleave', () => drpHoverDay(null));
  }
}

function renderDRP() {
  for (let i = 0; i < 2; i++) {
    let m = drpBaseMonth+i, y = drpBaseYear;
    if (m>11) { m-=12; y++; }
    renderMonth(i, y, m);
  }
  let m2 = drpBaseMonth+1, y2 = drpBaseYear;
  if (m2>11) { m2-=12; y2++; }
  document.getElementById('drp-header').textContent = `${MONTHS[drpBaseMonth]} ${drpBaseYear}  ·  ${MONTHS[m2]} ${y2}`;

  const hint = document.getElementById('drp-hint');
  const disp = document.getElementById('drp-range-display');

  if (!drpStart) {
    hint.textContent = 'Click a start date'; hint.style.display=''; disp.style.display='none';
  } else if (!drpEnd) {
    hint.textContent = `Start: ${fmtDate(drpStart)} — now click an end date`; hint.style.display=''; disp.style.display='none';
  } else {
    hint.style.display='none'; disp.style.display='';
    document.getElementById('drp-start-label').textContent = fmtDate(drpStart);
    document.getElementById('drp-end-label').textContent   = fmtDate(drpEnd);
    const nights = Math.round((drpEnd-drpStart)/86400000);
    document.getElementById('drp-duration').textContent = nights===0?'Same day rental':`${nights} night${nights!==1?'s':''}`;
    document.getElementById('drp-type-hint').textContent = nights>0?'→ Multi-Day rental':'→ Day rental';
  }
}

function updateHiddenFields(preserveDropdown) {
  if (!drpStart||!drpEnd) return;
  const st = document.getElementById('drp-start-time').value||'09:00';
  const et = document.getElementById('drp-end-time').value||'17:00';
  document.getElementById('f-start').value = `${dateKey(drpStart)}T${st}`;
  document.getElementById('f-end').value   = `${dateKey(drpEnd)}T${et}`;
  const nights = Math.round((drpEnd-drpStart)/86400000);
  const [sh,sm] = st.split(':').map(Number);
  const [eh,em] = et.split(':').map(Number);
  const durH = (eh+em/60)-(sh+sm/60)+(nights*24);
  const rtype = nights>0?'Multi-Day':durH<=4?'Hourly':durH<=6?'Half-Day':'Full-Day';
  const sel = document.getElementById('rental-type-sel');
  if (preserveDropdown && sel && sel.value) {
    // On step 3, sync hidden field FROM the dropdown — don't overwrite user's choice
    document.getElementById('f-rental-type').value = sel.value;
  } else {
    document.getElementById('f-rental-type').value = rtype;
    if (sel) sel.value = rtype;
  }
}

// ── Step navigation ────────────────────────────────────────────────────────
function goStep(n, fromPopState) {
  document.querySelectorAll('.wizard-step').forEach(el=>el.classList.remove('active'));
  document.getElementById(`step-${n}`).classList.add('active');
  for (let i=1;i<=3;i++) {
    document.getElementById(`wp-${i}`).className = 'wp-num'+(i===n?' active':i<n?' done':'');
    document.getElementById(`wl-${i}`).className = 'wp-label'+(i===n?' active':'');
  }
  for (let i=1;i<=2;i++) document.getElementById(`wline-${i}`).className='wp-line'+(i<n?' done':'');
  const labels = ['Select dates','Choose kayaks','Customer info & confirm'];
  document.getElementById('step-subtitle').textContent = `Step ${n} of 3 — ${labels[n-1]}`;
  if (n===2) renderEquipment();
  if (n===3) { updateHiddenFields(true); renderSummary(); updatePricing(); setTimeout(()=>document.getElementById('customer-name-input')?.focus(),100); }
  window.scrollTo({top:0,behavior:'smooth'});
  // Save step to sessionStorage (F-10) so back-navigation restores state
  _saveWizardState(n);
  // Push a history entry so the browser Back button navigates steps, not away (#16)
  if (!fromPopState) {
    history.pushState({wizardStep: n}, '', location.pathname + '?step=' + n);
  }
}

// Browser Back/Forward navigates between steps (#16)
window.addEventListener('popstate', e => {
  const step = e.state && e.state.wizardStep;
  if (step && step >= 1 && step <= 3) {
    goStep(step, true);
  } else {
    // No wizard state — let the browser navigate away normally
    history.go(-1);
  }
});

// ── sessionStorage persistence (F-10) ─────────────────────────────────────
const SS_KEY = 'pkr_wiz';

function _saveWizardState(step) {
  try {
    const st = document.getElementById('drp-start-time')?.value || '';
    const et = document.getElementById('drp-end-time')?.value || '';
    sessionStorage.setItem(SS_KEY, JSON.stringify({
      step,
      startDate:  drpStart ? dateKey(drpStart) : null,
      endDate:    drpEnd   ? dateKey(drpEnd)   : null,
      startTime:  st,
      endTime:    et,
      items:      [...selectedItems],
    }));
  } catch(e) {}
}

function _restoreWizardState() {
  try {
    const raw = sessionStorage.getItem(SS_KEY);
    if (!raw) return false;
    const s = JSON.parse(raw);
    if (s.startDate) { drpStart = parseDate(s.startDate); drpBaseMonth = drpStart.getMonth(); drpBaseYear = drpStart.getFullYear(); }
    if (s.endDate)   drpEnd   = parseDate(s.endDate);
    if (s.startTime) { const el = document.getElementById('drp-start-time'); if (el) el.value = s.startTime; }
    if (s.endTime)   { const el = document.getElementById('drp-end-time');   if (el) el.value = s.endTime; }
    for (const [id, qty] of (s.items || [])) selectedItems.set(id, qty);
    // Rebuild the hidden item_ids inputs (repeated per unit) so a restored
    // draft submits the same equipment it had before navigating away.
    const cont = document.getElementById('item-inputs');
    if (cont) {
      cont.innerHTML = '';
      for (const [iid, qty] of selectedItems) {
        for (let n = 0; n < qty; n++) {
          const inp = document.createElement('input');
          inp.type = 'hidden'; inp.name = 'item_ids'; inp.value = iid;
          cont.appendChild(inp);
        }
      }
    }
    return true;
  } catch(e) { return false; }
}

// ── Equipment ──────────────────────────────────────────────────────────────
function renderEquipment() {
  updateHiddenFields();
  const start = new Date(document.getElementById('f-start').value);
  const end   = new Date(document.getElementById('f-end').value);
  const booked = getBookedItems(start, end);

  document.getElementById('step2-dates').textContent =
    drpStart&&drpEnd ? `${fmtShort(drpStart)} → ${fmtShort(drpEnd)}` : '';

  const cats = {};
  for (const item of INVENTORY) {
    const c = item['Category']||'Other';
    (cats[c]||(cats[c]=[])).push(item);
  }

  let html = '';
  for (const [cat, items] of Object.entries(cats).sort()) {
    html += `<div style="margin-bottom:1.5rem">
      <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);margin-bottom:.5rem">${cat}</div>
      <div class="item-select-grid">`;
    for (const item of items) {
      const id  = item['Item ID'];
      const totalQty = parseInt(item['Quantity'], 10) || 1;
      const alreadyBooked = booked.get(id) || 0;
      const statusBlocked = item['Status']==='Maintenance' || item['Status']==='Rented';
      const available = statusBlocked ? 0 : Math.max(0, totalQty - alreadyBooked);
      const sel = selectedItems.get(id) || 0;
      const sz  = item['Size']||'';
      const szBadge = sz?`<span class="size-badge size-${sz.toLowerCase()}">${sz}</span>`:'';
      const unavail = available === 0 && sel === 0;

      let statusHtml;
      if (unavail) {
        statusHtml = `<span style="color:var(--orange-600);font-size:.73rem">⚠ Unavailable this period</span>`;
      } else if (totalQty > 1) {
        statusHtml = `<span style="color:var(--text-muted);font-size:.73rem">${available} of ${totalQty} available</span>`;
      } else {
        statusHtml = '';
      }

      html += `<div class="item-select-card${unavail?' unavailable':''}${sel>0?' selected':''}"
        data-id="${id.replace(/"/g,'&quot;')}" data-available="${available + sel}"
        style="${unavail?'cursor:not-allowed':''}">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:.5rem">
          <div style="min-width:0">
            <div class="item-name">${item['Name/Description']||id} ${szBadge}</div>
            <div class="item-meta">${id}</div>
            <div class="item-meta" style="margin-top:.15rem">${statusHtml}</div>
          </div>
          ${unavail ? '' : `
          <div style="display:flex;align-items:center;gap:.4rem;flex-shrink:0">
            <button type="button" class="btn btn-ghost btn-sm" style="padding:.15rem .55rem" onclick="equipAdjustQty('${id.replace(/'/g,"\\'")}', -1)">−</button>
            <span style="min-width:1.2rem;text-align:center;font-weight:700">${sel}</span>
            <button type="button" class="btn btn-ghost btn-sm" style="padding:.15rem .55rem" onclick="equipAdjustQty('${id.replace(/'/g,"\\'")}', 1)">+</button>
          </div>`}
        </div>
      </div>`;
    }
    html += `</div></div>`;
  }
  document.getElementById('equipment-grid').innerHTML = html;
  updateSelCount();
}

function equipAdjustQty(id, delta) {
  const card = document.querySelector(`.item-select-card[data-id="${id.replace(/"/g,'\\"')}"]`);
  const maxAvailable = card ? parseInt(card.dataset.available, 10) || 0 : 0;
  const current = selectedItems.get(id) || 0;
  const next = Math.max(0, Math.min(maxAvailable, current + delta));
  if (next === current) return;
  if (next === 0) selectedItems.delete(id);
  else selectedItems.set(id, next);

  // Sync hidden inputs — repeat the ID once per selected unit
  const cont = document.getElementById('item-inputs');
  cont.innerHTML = '';
  for (const [iid, qty] of selectedItems) {
    for (let n = 0; n < qty; n++) {
      const inp = document.createElement('input');
      inp.type='hidden'; inp.name='item_ids'; inp.value=iid;
      cont.appendChild(inp);
    }
  }
  updateSelCount();
  renderEquipment(); // re-render so the stepper count + "selected" styling refresh
  _saveWizardState(2);
}

function updateSelCount() {
  const n = totalSelectedUnits();
  const badge = document.getElementById('selected-count');
  badge.textContent = `${n} item${n!==1?'s':''} selected`;
  const btn3 = document.getElementById('btn-to-3');
  btn3.disabled = n===0;
  // Show/hide the "select an item" hint (#24)
  const hint = document.getElementById('step2-hint');
  if (hint) hint.style.display = n===0 ? '' : 'none';
}

// ── Summary & Pricing ──────────────────────────────────────────────────────
function renderSummary() {
  const nights = drpStart&&drpEnd ? Math.round((drpEnd-drpStart)/86400000) : 0;
  let html = `
    <div class="info-row"><span class="info-label">Dates</span>
      <span class="info-value" style="font-weight:600">${drpStart?fmtDate(drpStart):'—'} → ${drpEnd?fmtDate(drpEnd):'—'}</span></div>
    <div class="info-row"><span class="info-label">Time</span>
      <span class="info-value">${document.getElementById('drp-start-time').value||'09:00'} → ${document.getElementById('drp-end-time').value||'17:00'}</span></div>
    <div class="info-row"><span class="info-label">Duration</span>
      <span class="info-value">${nights===0?'Same day':`${nights} night${nights!==1?'s':''}`}</span></div>
    <div class="divider"></div>
    <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);margin-bottom:.35rem">Equipment</div>`;
  for (const [id, qty] of selectedItems) {
    const item = INVENTORY.find(i => i['Item ID'] === id);
    const label = item ? (item['Name/Description'] || id) : id;
    html += `<div style="font-size:.82rem;padding:.1rem 0">${label}${qty > 1 ? ` ×${qty}` : ''}</div>`;
  }
  document.getElementById('summary-body').innerHTML = html;
}

async function updatePricing() {
  const breakdownEl = document.getElementById('pricing-breakdown');
  if (!breakdownEl) return; // pricing tracking disabled — no breakdown UI to fill
  const idList = [];
  for (const [id, qty] of selectedItems) for (let n = 0; n < qty; n++) idList.push(id);
  const ids = idList.join(',');
  if (!ids) return;
  const start = document.getElementById('f-start').value;
  const end   = document.getElementById('f-end').value;
  const type  = document.getElementById('rental-type-sel')?.value || 'Full-Day';
  try {
    const data = await fetch(`/api/pricing?items=${encodeURIComponent(ids)}&type=${type}&start=${start}&end=${end}`).then(r=>r.json());
    if (data.error) return;
    const isCLP = data.currency === 'CLP';
    const fmtAmt = (n) => isCLP ? `CLP $${Math.round(n).toLocaleString()}` : `$${n.toFixed(2)}`;
    let html = '<div style="border-top:1px solid var(--border);padding-top:.75rem;margin-top:.5rem">';
    for (const b of data.breakdown) {
      const label = b.qty > 1 ? `${b.name||b.item_id} ×${b.qty}` : (b.name||b.item_id);
      html += `<div class="info-row" style="font-size:.8rem"><span class="info-label">${label}</span><span class="info-value">${fmtAmt(b.subtotal)}</span></div>`;
    }
    html += `<div class="info-row" style="font-weight:700;margin-top:.25rem;font-size:.95rem">
      <span class="info-label">Estimated Total</span>
      <span class="info-value" style="color:var(--brand-dark)">${fmtAmt(data.total)}</span></div></div>`;
    breakdownEl.innerHTML = html;
    const amt = document.getElementById('payment-amount');
    // Always recalculate so the amount tracks the current dates/equipment selection.
    if (amt) amt.value = isCLP ? Math.round(data.total) : data.total.toFixed(2);
  } catch(e) {}
}

// ── Init ───────────────────────────────────────────────────────────────────
// Only restore sessionStorage state when the user actually navigated back/forward
// within this same draft (e.g. browser Back button). A fresh "+ New Booking" click
// is a normal "navigate" entry, not "back_forward" — restoring there leaked a
// previous guest's equipment selection into the next guest's booking.
const _navEntries = performance.getEntriesByType && performance.getEntriesByType('navigation');
const _isBackForward = _navEntries && _navEntries[0] && _navEntries[0].type === 'back_forward';
if (_isBackForward) {
  _restoreWizardState();
} else {
  try { sessionStorage.removeItem(SS_KEY); } catch(e) {}
}

// Clear the draft the moment the booking is actually submitted, so a stray
// back-navigation afterward never resurrects it into the next booking.
document.getElementById('res-form')?.addEventListener('submit', () => {
  try { sessionStorage.removeItem(SS_KEY); } catch(e) {}
});

// Seed the history stack so the very first Back press goes to Step 1, not away
history.replaceState({wizardStep: 1}, '', location.pathname + '?step=1');

renderDRP();
initDRPListeners();
if (drpStart && drpEnd) updateHiddenFields();

// ── Restore state after server-side validation error (#3) ──────────────────
(function restoreAfterError() {
  const rd = JSON.parse(document.getElementById('restore-data').textContent);
  if (!rd.restore) return;

  // Restore dates
  if (rd.start) {
    const [datePart, timePart] = rd.start.split('T');
    if (datePart) drpStart = parseDate(datePart);
    if (timePart) document.getElementById('drp-start-time').value = timePart.slice(0,5);
  }
  if (rd.end) {
    const [datePart, timePart] = rd.end.split('T');
    if (datePart) drpEnd = parseDate(datePart);
    if (timePart) document.getElementById('drp-end-time').value = timePart.slice(0,5);
  }
  if (drpStart) {
    drpBaseMonth = drpStart.getMonth();
    drpBaseYear = drpStart.getFullYear();
  }
  renderDRP();
  if (drpStart && drpEnd) updateHiddenFields();

  // Restore selected items — rd.items is a flat list with an ID repeated once
  // per booked unit, so count occurrences back into quantities.
  for (const id of (rd.items || [])) selectedItems.set(id, (selectedItems.get(id) || 0) + 1);
  // Rebuild the hidden item_ids inputs so re-submitting from step 3 keeps them.
  const _restoreCont = document.getElementById('item-inputs');
  if (_restoreCont) {
    _restoreCont.innerHTML = '';
    for (const [iid, qty] of selectedItems) {
      for (let n = 0; n < qty; n++) {
        const inp = document.createElement('input');
        inp.type = 'hidden'; inp.name = 'item_ids'; inp.value = iid;
        _restoreCont.appendChild(inp);
      }
    }
  }

  // Pre-fill customer fields
  if (rd.name)   document.querySelector('[name=customer_name]').value = rd.name;
  if (rd.phone)  document.querySelector('[name=customer_phone]').value = rd.phone;
  if (rd.email)  document.querySelector('[name=customer_email]').value = rd.email;
  if (rd.notes)  document.querySelector('[name=notes]').value = rd.notes;
  if (rd.payment_status) { const el = document.querySelector('[name=payment_status]'); if (el) el.value = rd.payment_status; }
  if (rd.payment_amount) { const el = document.getElementById('payment-amount'); if (el) el.value = rd.payment_amount; }
  if (rd.waiver) document.querySelector('[name=waiver_signed]').checked = true;

  // Jump directly to Step 3 so staff see the errors in context
  goStep(3);
  // Scroll flash messages into view
  setTimeout(() => {
    const flash = document.querySelector('.flash-messages');
    if (flash) flash.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 150);
})();
