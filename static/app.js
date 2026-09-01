const CATEGORY_LABELS = {
  GT: 'Grand Total Revenue',
  MOC: 'MOC Revenue',
  MTC: 'MTC Revenue',
  DATA: 'Data Revenue',
  SMS: 'SMS Revenue'
};

const CATEGORY_COLORS = {
  GT: '#5a9a3c',
  MOC: '#3b7dd8',
  MTC: '#e0a72f',
  DATA: '#8a5cd8',
  SMS: '#d85c8a'
};

// `state` holds the filters/category that are actually in effect (i.e. what
// the last successful fetch used) — NOT necessarily what's sitting in the
// filter dropdowns right now. Switching category tabs re-fetches using
// these same division/district/month_key values, which is what keeps
// filters "sticky" across tabs (req #3).
let state = {
  category: 'DASHBOARD',
  month_key: null,   // 'YYYYMM' — set once /api/months loads
  division: '',
  district: '',
  upazila: ''
};

// Populated once from /api/filters — { divisions, districts_by_division,
// upazilas_by_division_district }. Kept around so the Upazila dropdown can
// be rebuilt any time Division/District change, without re-fetching.
let filterData = null;

// Snapshot of the filter values that produced the data currently on
// screen. The Apply button's Applied/dirty state is just "do the live
// dropdown values match this?" — see syncApplyButtonState().
let appliedSnapshot = null;

let charts = { dailyTrend: null, share: null, division: null };

// ═══════════════════════════════════════════════════════════════
//  RACE-CONDITION-SAFE FETCH
//  Every data fetch goes through here. Starting a new request aborts
//  whatever request was previously in flight and bumps a request id;
//  a response is only accepted if its id is still the latest one when
//  it comes back. So "click MTC, then quickly click SMS" can never let
//  the stale MTC response overwrite the newer SMS result — either the
//  MTC request gets aborted outright, or (if it had already reached the
//  server) its response is simply discarded as stale.
// ═══════════════════════════════════════════════════════════════
let requestSeq = 0;
let activeRequestId = 0;
let activeController = null;

async function fetchJson(url) {
  const myId = ++requestSeq;
  activeRequestId = myId;
  if (activeController) activeController.abort();
  const controller = new AbortController();
  activeController = controller;

  try {
    const res = await fetch(url, { signal: controller.signal });
    if (myId !== activeRequestId) return { stale: true };
    const data = await res.json();
    if (myId !== activeRequestId) return { stale: true };
    if (!res.ok || data.error) {
      return { error: data.error || `Request failed (HTTP ${res.status})` };
    }
    return { data };
  } catch (e) {
    if (e.name === 'AbortError' || myId !== activeRequestId) return { stale: true };
    return { error: e.message || 'Network error' };
  }
}

async function loadMonthOptions() {
  try {
    const res = await fetch('/api/months');
    const months = await res.json();
    if (months.error) throw new Error(months.error);

    const monthSel = document.getElementById('monthFilter');
    monthSel.innerHTML = '';
    months.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.month_key;
      opt.textContent = m.label;
      monthSel.appendChild(opt);
    });

    if (months.length > 0) {
      state.month_key = months[0].month_key;  // newest first
      monthSel.value = state.month_key;
    }
  } catch (e) {
    showStatus('Could not load available months: ' + e.message);
  }
}

// Rebuilds the Upazila dropdown to exactly the set of upazilas that exist
// under the currently-selected Division (+ District, if also selected).
// - Division + District selected  -> upazilas under that exact pair
// - Division only selected        -> union of upazilas across all its districts
// - Neither selected              -> "All Upazilas" only (nothing else to scope to)
function populateUpazilaOptions(division, district) {
  const upazilaSel = document.getElementById('upazilaFilter');
  const previousValue = upazilaSel.value;
  upazilaSel.innerHTML = '<option value="">All Upazilas</option>';

  if (!filterData || !division) {
    syncApplyButtonState();
    return;
  }

  const byDistrict = filterData.upazilas_by_division_district[division] || {};
  let upazilas;
  if (district) {
    upazilas = byDistrict[district] || [];
  } else {
    const combined = new Set();
    Object.values(byDistrict).forEach(list => list.forEach(u => combined.add(u)));
    upazilas = Array.from(combined).sort();
  }

  upazilas.forEach(u => {
    const opt = document.createElement('option');
    opt.value = u;
    opt.textContent = u;
    upazilaSel.appendChild(opt);
  });

  // Keep the previous selection if it's still a valid option under the new
  // Division/District scope (e.g. re-selecting the same district).
  if (upazilas.includes(previousValue)) upazilaSel.value = previousValue;
  syncApplyButtonState();
}

async function loadFilterOptions() {
  try {
    const res = await fetch('/api/filters');
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    filterData = data;

    const divisionSel = document.getElementById('divisionFilter');
    data.divisions.forEach(div => {
      const opt = document.createElement('option');
      opt.value = div;
      opt.textContent = div;
      divisionSel.appendChild(opt);
    });

    const districtSel = document.getElementById('districtFilter');

    divisionSel.addEventListener('change', () => {
      districtSel.innerHTML = '<option value="">All Districts</option>';
      const districts = data.districts_by_division[divisionSel.value] || [];
      districts.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d;
        opt.textContent = d;
        districtSel.appendChild(opt);
      });
      populateUpazilaOptions(divisionSel.value, '');
    });

    districtSel.addEventListener('change', () => {
      populateUpazilaOptions(divisionSel.value, districtSel.value);
    });
  } catch (e) {
    showStatus('Could not load Division/District/Upazila filters: ' + e.message);
  }
}

function showStatus(msg) {
  const el = document.getElementById('statusMessage');
  el.style.display = 'block';
  el.textContent = msg;
}

function hideStatus() {
  document.getElementById('statusMessage').style.display = 'none';
}

function commonParams() {
  const params = new URLSearchParams({ month: state.month_key });
  if (state.division) params.append('division', state.division);
  if (state.district) params.append('district', state.district);
  if (state.upazila) params.append('upazila', state.upazila);
  return params;
}

// ═══════════════════════════════════════════════════════════════
//  VIEW STATE: loading / error / data
//  "Do not silently leave the previous result looking like the new
//  result" — showLoading() hides both the dashboard and table views
//  the instant a fetch starts, so stale data is never mistaken for
//  the answer to the new request.
// ═══════════════════════════════════════════════════════════════
function showLoading(message) {
  document.getElementById('loadingMessage').textContent = message;
  document.getElementById('loadingState').style.display = 'flex';
  document.getElementById('errorState').style.display = 'none';
  document.getElementById('dashboardView').style.display = 'none';
  document.getElementById('tableView').style.display = 'none';
  document.getElementById('downloadCsv').disabled = true;
}

function showError(message, retryFn) {
  document.getElementById('errorMessage').textContent = message;
  document.getElementById('errorState').style.display = 'flex';
  document.getElementById('loadingState').style.display = 'none';
  document.getElementById('dashboardView').style.display = 'none';
  document.getElementById('tableView').style.display = 'none';
  document.getElementById('downloadCsv').disabled = true;
  document.getElementById('retryButton').onclick = retryFn;
}

function showView(view) {
  document.getElementById('loadingState').style.display = 'none';
  document.getElementById('errorState').style.display = 'none';
  document.getElementById('dashboardView').style.display = view === 'dashboard' ? '' : 'none';
  document.getElementById('tableView').style.display = view === 'table' ? '' : 'none';
  document.getElementById('downloadCsv').disabled = false;
}

function loadingMessageFor(category) {
  return category === 'DASHBOARD' ? 'Loading Dashboard…' : `Loading ${CATEGORY_LABELS[category]}…`;
}

// Returns true on success, false on failure/stale — callers (Apply button)
// use this to decide whether to advance to the "✓ Applied" state.
async function loadCurrentView(loadingMessage) {
  if (!state.month_key) return false;  // months haven't loaded yet
  showLoading(loadingMessage || loadingMessageFor(state.category));

  if (state.category === 'DASHBOARD') {
    return loadDashboard();
  }
  return loadTableReport();
}

async function loadTableReport() {
  hideStatus();
  document.getElementById('cardTitle').textContent = CATEGORY_LABELS[state.category];

  const params = commonParams();
  params.append('category', state.category);

  const result = await fetchJson('/api/revenue-report?' + params.toString());
  if (result.stale) return false;
  if (result.error) {
    showError('Failed to load report: ' + result.error, () => loadCurrentView());
    return false;
  }
  renderTable(result.data);
  showView('table');
  return true;
}

function fmtMoney(n) {
  return Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

async function loadDashboard() {
  hideStatus();
  const result = await fetchJson('/api/dashboard-summary?' + commonParams().toString());
  if (result.stale) return false;
  if (result.error) {
    showError('Failed to load dashboard: ' + result.error, () => loadCurrentView());
    return false;
  }
  renderDashboard(result.data);
  showView('dashboard');
  return true;
}

function renderDashboard(data) {
  const kpiGrid = document.getElementById('kpiGrid');
  kpiGrid.innerHTML = '';
  Object.keys(CATEGORY_LABELS).forEach(cat => {
    const div = document.createElement('div');
    div.className = 'kpi-card';
    div.style.borderTopColor = CATEGORY_COLORS[cat];
    div.innerHTML = `
      <div class="kpi-label">${CATEGORY_LABELS[cat]}</div>
      <div class="kpi-value">${fmtMoney(data.category_totals[cat])}</div>
    `;
    kpiGrid.appendChild(div);
  });
  const siteCard = document.createElement('div');
  siteCard.className = 'kpi-card';
  siteCard.style.borderTopColor = '#7a8087';
  siteCard.innerHTML = `
    <div class="kpi-label">Active Sites</div>
    <div class="kpi-value">${fmtMoney(data.site_count)}</div>
  `;
  kpiGrid.appendChild(siteCard);

  renderDailyTrendChart(data.daily_trend);
  renderShareChart(data.category_totals);
  renderDivisionChart(data.division_breakdown);
  renderDistrictTable(data.district_breakdown);

  document.getElementById('lastUpdated').textContent = '● Updated ' + new Date().toLocaleTimeString();
}

function renderDailyTrendChart(dailyTrend) {
  const ctx = document.getElementById('dailyTrendChart');
  if (charts.dailyTrend) charts.dailyTrend.destroy();
  charts.dailyTrend = new Chart(ctx, {
    type: 'line',
    data: {
      labels: dailyTrend.map(d => d.day),
      datasets: [{
        label: 'GT Revenue',
        data: dailyTrend.map(d => d.value),
        borderColor: CATEGORY_COLORS.GT,
        backgroundColor: 'rgba(90,154,60,0.12)',
        fill: true,
        tension: 0.3,
        pointRadius: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { title: { display: true, text: 'Day of month' } } }
    }
  });
}

function renderShareChart(categoryTotals) {
  const cats = ['MOC', 'MTC', 'DATA', 'SMS'];
  const ctx = document.getElementById('shareChart');
  if (charts.share) charts.share.destroy();
  charts.share = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: cats.map(c => CATEGORY_LABELS[c]),
      datasets: [{
        data: cats.map(c => categoryTotals[c]),
        backgroundColor: cats.map(c => CATEGORY_COLORS[c])
      }]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
  });
}

function renderDivisionChart(divisionBreakdown) {
  const ctx = document.getElementById('divisionChart');
  if (charts.division) charts.division.destroy();
  const avgPerSite = divisionBreakdown.map(d => d.site_count ? d.value / d.site_count : 0);
  charts.division = new Chart(ctx, {
    data: {
      labels: divisionBreakdown.map(d => d.division),
      datasets: [
        { type: 'bar', label: 'Total Revenue', data: divisionBreakdown.map(d => d.value), backgroundColor: CATEGORY_COLORS.GT, yAxisID: 'y' },
        { type: 'line', label: 'Avg Revenue / Site', data: avgPerSite, borderColor: CATEGORY_COLORS.MTC, backgroundColor: CATEGORY_COLORS.MTC, yAxisID: 'y1', tension: 0.3 }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { type: 'linear', position: 'left', title: { display: true, text: 'Total Revenue' } },
        y1: { type: 'linear', position: 'right', title: { display: true, text: 'Avg Revenue / Site' }, grid: { drawOnChartArea: false } }
      }
    }
  });
}

function renderDistrictTable(districtBreakdown) {
  const tbody = document.getElementById('districtTableBody');
  tbody.innerHTML = '';
  districtBreakdown.forEach(d => {
    const tr = document.createElement('tr');
    const avg = d.site_count ? d.value / d.site_count : 0;
    tr.innerHTML = `
      <td class="sticky-col">${d.district}</td>
      <td>${fmtMoney(d.site_count)}</td>
      <td>${fmtMoney(d.value)}</td>
      <td>${fmtMoney(avg)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderTable(data) {
  const headRow = document.getElementById('tableHeadRow');
  headRow.innerHTML = `
    <th class="sticky-col">Division</th>
    <th class="sticky-col">District</th>
    <th class="sticky-col">Upazila</th>
    <th class="sticky-col">Site ID</th>
  `;
  for (let d = 1; d <= data.days_in_month; d++) {
    const th = document.createElement('th');
    th.textContent = d;
    headRow.appendChild(th);
  }

  const tbody = document.getElementById('tableBody');
  tbody.innerHTML = '';

  data.rows.forEach(row => {
    const tr = document.createElement('tr');
    tr.dataset.site = (row.SITE_ID ?? '').toString();
    tr.dataset.upazila = (row.UPAZILA ?? '').toString();
    tr.innerHTML = `
      <td class="sticky-col" title="${row.DIVISION ?? ''}">${row.DIVISION ?? ''}</td>
      <td class="sticky-col" title="${row.DISTRICT ?? ''}">${row.DISTRICT ?? ''}</td>
      <td class="sticky-col" title="${row.UPAZILA ?? ''}">${row.UPAZILA ?? ''}</td>
      <td class="sticky-col" title="${row.SITE_ID ?? ''}">${row.SITE_ID ?? ''}</td>
    `;
    for (let d = 1; d <= data.days_in_month; d++) {
      const val = row['D' + d];
      const td = document.createElement('td');
      td.textContent = (val === null || val === undefined) ? '-' : Number(val).toLocaleString(undefined, {maximumFractionDigits: 2});
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  });

  document.getElementById('siteCount').textContent = data.rows.length;
  document.getElementById('lastUpdated').textContent = '● Updated ' + new Date().toLocaleTimeString();

  // Re-apply whatever search term is already active (e.g. switching tabs
  // keeps the same site/upazila highlighted) — no scroll-into-view here,
  // only on-highlight fires that (see runSiteSearch).
  applySiteHighlight(false);
}

function setupTabs() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.classList.contains('active')) return;
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.category = btn.dataset.category;
      updateSiteSearchVisibility();
      // Filters (division/district/month) are NOT touched here — switching
      // categories reuses whatever is already applied (req #3).
      loadCurrentView();
    });
  });
}

// ═══════════════════════════════════════════════════════════════
//  APPLY BUTTON: Apply Filters → Processing… → ✓ Applied
//  "Applied" means the displayed data matches the CURRENT dropdown
//  values — so any dropdown change immediately falls back out of the
//  Applied state, even before the user clicks Apply again.
// ═══════════════════════════════════════════════════════════════
function currentFilterSelection() {
  return {
    division: document.getElementById('divisionFilter').value,
    district: document.getElementById('districtFilter').value,
    upazila: document.getElementById('upazilaFilter').value,
    month_key: document.getElementById('monthFilter').value,
  };
}

function selectionsEqual(a, b) {
  return !!a && !!b && a.division === b.division && a.district === b.district
    && a.upazila === b.upazila && a.month_key === b.month_key;
}

function syncApplyButtonState() {
  const btn = document.getElementById('applyFilters');
  if (btn.dataset.busy === '1') return;  // mid-request; leave the Processing label alone

  if (selectionsEqual(currentFilterSelection(), appliedSnapshot)) {
    btn.textContent = '✓ Applied';
    btn.classList.remove('processing');
    btn.classList.add('applied');
    btn.disabled = true;
  } else {
    btn.textContent = 'Apply Filters';
    btn.classList.remove('processing', 'applied');
    btn.disabled = false;
  }
}

function setupApplyButton() {
  const btn = document.getElementById('applyFilters');
  btn.addEventListener('click', async () => {
    const selection = currentFilterSelection();

    btn.dataset.busy = '1';
    btn.textContent = 'Processing...';
    btn.classList.remove('applied');
    btn.classList.add('processing');
    btn.disabled = true;

    state.division = selection.division;
    state.district = selection.district;
    state.upazila = selection.upazila;
    state.month_key = selection.month_key;

    const ok = await loadCurrentView('Applying current filters...');

    btn.dataset.busy = '0';
    if (ok) {
      appliedSnapshot = selection;
    }
    syncApplyButtonState();
  });

  ['divisionFilter', 'districtFilter', 'upazilaFilter', 'monthFilter'].forEach(id => {
    document.getElementById(id).addEventListener('change', syncApplyButtonState);
  });
}

function setupCsvButton() {
  document.getElementById('downloadCsv').addEventListener('click', () => {
    if (document.getElementById('downloadCsv').disabled) return;
    const params = commonParams();
    params.append('category', state.category === 'DASHBOARD' ? 'GT' : state.category);
    window.location.href = '/api/revenue-report/csv?' + params.toString();
  });
}

// ═══════════════════════════════════════════════════════════════
//  SITE ID SEARCH
//  Doesn't filter rows out — the table can be hundreds of rows long
//  sorted by Upazila-then-Site-ID, so a row a user expects to find
//  "near" another one (alphabetically) can actually sit far down the
//  table. Instead, matches are highlighted in place and the first
//  match is scrolled into view, so the surrounding context (division/
//  district/other sites) stays visible.
//
//  Matches on Site ID only (Upazila now has its own dropdown filter up
//  top). The search only runs when the user clicks Search / presses
//  Enter — not on every keystroke — so typing a single letter doesn't
//  jump the page around before the user has finished typing.
// ═══════════════════════════════════════════════════════════════
let siteSearchTerm = '';

function applySiteHighlight(scrollToFirst) {
  const tbody = document.getElementById('tableBody');
  const countEl = document.getElementById('searchMatchCount');
  const clearBtn = document.getElementById('siteSearchClear');
  const rows = tbody ? Array.from(tbody.querySelectorAll('tr')) : [];

  clearBtn.style.display = siteSearchTerm ? '' : 'none';

  if (!siteSearchTerm) {
    rows.forEach(tr => tr.classList.remove('row-highlight'));
    countEl.style.display = 'none';
    return;
  }

  let firstMatch = null;
  let matchCount = 0;
  rows.forEach(tr => {
    const site = (tr.dataset.site || '').toLowerCase();
    const isMatch = site.includes(siteSearchTerm);
    tr.classList.toggle('row-highlight', isMatch);
    if (isMatch) {
      matchCount++;
      if (!firstMatch) firstMatch = tr;
    }
  });

  countEl.style.display = '';
  countEl.textContent = matchCount === 0
    ? 'No matches'
    : `${matchCount} match${matchCount > 1 ? 'es' : ''}`;

  if (scrollToFirst && firstMatch) {
    firstMatch.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function runSiteSearch() {
  const input = document.getElementById('siteSearch');
  siteSearchTerm = input.value.trim().toLowerCase();
  applySiteHighlight(true);
}

function setupSiteSearch() {
  const input = document.getElementById('siteSearch');
  const searchBtn = document.getElementById('siteSearchBtn');
  const clearBtn = document.getElementById('siteSearchClear');

  searchBtn.addEventListener('click', runSiteSearch);

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      runSiteSearch();
    }
  });

  clearBtn.addEventListener('click', () => {
    input.value = '';
    siteSearchTerm = '';
    applySiteHighlight(false);
    input.focus();
  });
}

// Site ID search only makes sense on the per-category site table, not the
// aggregated Dashboard — hide the whole search group on Dashboard.
function updateSiteSearchVisibility() {
  const group = document.getElementById('siteSearchGroup');
  group.style.display = state.category === 'DASHBOARD' ? 'none' : '';
}

async function init() {
  setupTabs();
  setupApplyButton();
  setupCsvButton();
  setupSiteSearch();
  updateSiteSearchVisibility();
  await loadMonthOptions();
  await loadFilterOptions();

  appliedSnapshot = currentFilterSelection();
  const ok = await loadCurrentView();
  if (!ok) appliedSnapshot = null;
  syncApplyButtonState();
}

init();