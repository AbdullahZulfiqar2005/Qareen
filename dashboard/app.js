const API = '';
const PAGE_SIZE = 50;

let currentPage = 0;
let currentType = '';
let currentApp = '';
let totalEvents = 0;

// Tab switching
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'analytics') loadAnalytics();
  });
});

// --- Badge helpers ---
function badgeClass(type) {
  const map = {
    window: 'badge-window', shell: 'badge-shell', browser: 'badge-browser',
    tabs: 'badge-tabs', syslog: 'badge-syslog', keybind: 'badge-keybind',
    notify: 'badge-notify', file: 'badge-file', query: 'badge-query',
  };
  return map[type] || 'badge-default';
}

function eventCard(ev) {
  return `<div class="event-card">
    <span class="event-badge ${badgeClass(ev.event_type)}">${ev.event_type}</span>
    <div class="event-meta">
      <div class="event-content">${escHtml(ev.content)}</div>
      <div class="event-footer">
        <span>${escHtml(ev.timestamp)}</span>
        ${ev.app ? `<span>${escHtml(ev.app)}</span>` : ''}
      </div>
    </div>
  </div>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// --- Timeline ---
async function loadTimeline() {
  const offset = currentPage * PAGE_SIZE;
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
  if (currentType) params.set('type', currentType);
  if (currentApp) params.set('app', currentApp);

  const res = await fetch(`${API}/api/events?${params}`);
  const data = await res.json();
  totalEvents = data.total || 0;

  const list = document.getElementById('timeline-list');
  if (!data.events || data.events.length === 0) {
    list.innerHTML = '<div class="empty">No events found.</div>';
  } else {
    list.innerHTML = data.events.map(eventCard).join('');
  }

  document.getElementById('page-info').textContent =
    `${offset + 1}–${Math.min(offset + PAGE_SIZE, totalEvents)} of ${totalEvents}`;
  document.getElementById('prev-page').disabled = currentPage === 0;
  document.getElementById('next-page').disabled = offset + PAGE_SIZE >= totalEvents;
}

async function populateFilters() {
  const res = await fetch(`${API}/api/stats`);
  const data = await res.json();

  const typeSelect = document.getElementById('filter-type');
  (data.by_type || []).forEach(({ label }) => {
    const opt = document.createElement('option');
    opt.value = label; opt.textContent = label;
    typeSelect.appendChild(opt);
  });

  const appSelect = document.getElementById('filter-app');
  (data.top_apps || []).forEach(({ label }) => {
    const opt = document.createElement('option');
    opt.value = label; opt.textContent = label;
    appSelect.appendChild(opt);
  });
}

document.getElementById('filter-apply').addEventListener('click', () => {
  currentPage = 0;
  currentType = document.getElementById('filter-type').value;
  currentApp = document.getElementById('filter-app').value;
  loadTimeline();
});

document.getElementById('prev-page').addEventListener('click', () => {
  if (currentPage > 0) { currentPage--; loadTimeline(); }
});

document.getElementById('next-page').addEventListener('click', () => {
  currentPage++; loadTimeline();
});

// --- Search ---
async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;
  const container = document.getElementById('search-results');
  container.innerHTML = '<div class="empty">Searching…</div>';

  const res = await fetch(`${API}/api/search?q=${encodeURIComponent(q)}&k=12`);
  const data = await res.json();

  if (!data.results || data.results.length === 0) {
    container.innerHTML = '<div class="empty">No results found.</div>';
    return;
  }

  const maxScore = Math.max(...data.results.map(r => r.score), 0.001);
  container.innerHTML = data.results.map(r => `
    <div class="search-result">
      <div class="event-footer" style="margin-bottom:4px">
        <span class="event-badge ${badgeClass(r.event_type)}">${r.event_type}</span>
        <span>${escHtml(r.timestamp)}</span>
        ${r.app ? `<span>${escHtml(r.app)}</span>` : ''}
        <span style="margin-left:auto;color:var(--accent)">${(r.score * 100).toFixed(1)}%</span>
      </div>
      <div class="event-content">${escHtml(r.content)}</div>
      <div class="score-bar"><div class="score-fill" style="width:${(r.score/maxScore*100).toFixed(1)}%"></div></div>
    </div>`).join('');
}

document.getElementById('search-btn').addEventListener('click', doSearch);
document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});

// --- Analytics ---
function barChart(containerId, items, color) {
  const el = document.getElementById(containerId);
  if (!items || items.length === 0) { el.innerHTML = '<div class="empty">No data.</div>'; return; }
  const max = Math.max(...items.map(i => i.count), 1);
  el.innerHTML = items.map(({ label, count }) => `
    <div class="bar-row">
      <div class="bar-label" title="${escHtml(label)}">${escHtml(label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(count/max*100).toFixed(1)}%;background:${color||'var(--accent)'}"></div></div>
      <div class="bar-count">${count}</div>
    </div>`).join('');
}

function hourChart(containerId, hours) {
  const el = document.getElementById(containerId);
  const max = Math.max(...hours.map(h => h.count), 1);
  el.innerHTML = hours.map(({ hour, count }) => `
    <div class="hour-bar">
      <div class="hour-fill" style="height:${(count/max*100).toFixed(1)}%" title="${hour}:00 — ${count} events"></div>
      <div class="hour-label">${hour}</div>
    </div>`).join('');
}

async function loadAnalytics() {
  const res = await fetch(`${API}/api/stats`);
  const data = await res.json();

  document.getElementById('stat-cards').innerHTML = `
    <div class="stat-card"><div class="stat-value">${(data.total||0).toLocaleString()}</div><div class="stat-label">Total Events</div></div>
    <div class="stat-card"><div class="stat-value">${(data.today||0).toLocaleString()}</div><div class="stat-label">Today</div></div>
    <div class="stat-card"><div class="stat-value">${(data.this_week||0).toLocaleString()}</div><div class="stat-label">This Week</div></div>
    <div class="stat-card"><div class="stat-value">${(data.unique_apps||0).toLocaleString()}</div><div class="stat-label">Unique Apps</div></div>`;

  hourChart('chart-hour', data.by_hour || []);
  barChart('chart-type', data.by_type, 'var(--accent)');
  barChart('chart-app', data.top_apps, 'var(--green)');
  barChart('chart-daily', (data.by_day || []).map(d => ({ label: d.date.slice(5), count: d.count })), 'var(--purple)');
}

// Init
populateFilters();
loadTimeline();
