"""
Dashboard HTML page — the SaaS monitoring UI.

Served at /dashboard. Calls internal JSON endpoints at /dashboard/api/*.
Single-file, no build step. Dark theme, clean, functional.
"""

from fastapi.responses import HTMLResponse

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bounty — Social Intelligence Dashboard</title>
  <link rel="icon" href="/favicon.ico" sizes="any" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    :root {
      --bg: #0a0b0d;
      --surface: #141518;
      --surface2: #1c1e22;
      --border: #2a2d33;
      --text: #e8e8e8;
      --text-dim: #8b8d93;
      --accent: #4a9eff;
      --accent-dim: #2a6bd0;
      --green: #4ade80;
      --red: #f87171;
      --yellow: #fbbf24;
      --purple: #a78bfa;
      --radius: 8px;
    }
    body {
      font-family: 'Inter', -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }
    .mono { font-family: 'JetBrains Mono', monospace; }

    /* Layout */
    .app { display: flex; min-height: 100vh; }
    .sidebar {
      width: 240px;
      background: var(--surface);
      border-right: 1px solid var(--border);
      padding: 20px 0;
      flex-shrink: 0;
    }
    .sidebar-logo {
      padding: 0 24px 24px;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .sidebar-logo span { color: var(--accent); }
    .nav-item {
      padding: 10px 24px;
      color: var(--text-dim);
      cursor: pointer;
      font-size: 14px;
      font-weight: 500;
      transition: all 0.15s;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .nav-item:hover { color: var(--text); background: var(--surface2); }
    .nav-item.active { color: var(--accent); background: var(--surface2); border-right: 2px solid var(--accent); }
    .main { flex: 1; padding: 32px 40px; max-width: 1200px; }

    /* Header */
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; }
    .header h1 { font-size: 24px; font-weight: 600; }
    .header-actions { display: flex; gap: 12px; }

    /* Buttons */
    .btn {
      padding: 8px 16px;
      border-radius: var(--radius);
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      border: 1px solid var(--border);
      background: var(--surface2);
      color: var(--text);
      transition: all 0.15s;
    }
    .btn:hover { border-color: var(--accent); }
    .btn-primary {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }
    .btn-primary:hover { background: var(--accent-dim); }
    .btn-sm { padding: 4px 10px; font-size: 12px; }
    .btn-danger { color: var(--red); }
    .btn-danger:hover { border-color: var(--red); }

    /* Stats cards */
    .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 28px; }
    .stat-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
    }
    .stat-label { font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
    .stat-value { font-size: 28px; font-weight: 600; }

    /* Zones list */
    .section-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
    .zone-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      margin-bottom: 12px;
      transition: border-color 0.15s;
    }
    .zone-card:hover { border-color: var(--text-dim); }
    .zone-header { display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px; }
    .zone-name { font-size: 16px; font-weight: 600; }
    .zone-desc { font-size: 13px; color: var(--text-dim); margin-top: 4px; }
    .zone-keywords { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .keyword-tag {
      background: var(--surface2);
      padding: 3px 10px;
      border-radius: 12px;
      font-size: 12px;
      color: var(--text-dim);
    }
    .zone-meta { display: flex; gap: 16px; margin-top: 12px; font-size: 12px; color: var(--text-dim); }
    .zone-actions { display: flex; gap: 8px; }

    /* Badge */
    .badge {
      padding: 2px 8px;
      border-radius: 10px;
      font-size: 11px;
      font-weight: 500;
    }
    .badge-active { background: rgba(74,222,128,0.15); color: var(--green); }
    .badge-paused { background: rgba(138,141,147,0.15); color: var(--text-dim); }
    .badge-due { background: rgba(251,191,36,0.15); color: var(--yellow); }

    /* Alert */
    .alert-item {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px 20px;
      margin-bottom: 8px;
      display: flex;
      align-items: start;
      gap: 12px;
    }
    .alert-type {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      padding: 2px 8px;
      border-radius: 4px;
      flex-shrink: 0;
    }
    .alert-new { background: rgba(74,159,255,0.15); color: var(--accent); }
    .alert-growing { background: rgba(74,222,128,0.15); color: var(--green); }
    .alert-shrinking { background: rgba(248,113,113,0.15); color: var(--red); }
    .alert-content { flex: 1; }
    .alert-label { font-size: 14px; font-weight: 500; }
    .alert-meta { font-size: 12px; color: var(--text-dim); margin-top: 4px; }

    /* Cluster */
    .cluster-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px 20px;
      margin-bottom: 8px;
    }
    .cluster-header { display: flex; justify-content: space-between; align-items: center; }
    .cluster-label { font-size: 14px; font-weight: 500; }
    .cluster-stats { display: flex; gap: 12px; font-size: 12px; color: var(--text-dim); }
    .cluster-samples { margin-top: 12px; }
    .sample-post {
      padding: 8px 0;
      border-top: 1px solid var(--border);
      font-size: 13px;
    }
    .sample-post:first-child { border-top: none; }
    .sample-meta { color: var(--text-dim); font-size: 11px; margin-bottom: 4px; }

    /* Enrichment */
    .enrich-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
    .enrich-label { width: 100px; font-size: 12px; color: var(--text-dim); text-transform: capitalize; }
    .enrich-fill { height: 20px; background: var(--accent); border-radius: 3px; min-width: 4px; }
    .enrich-count { font-size: 12px; color: var(--text-dim); }

    /* Modal */
    .modal-overlay {
      position: fixed; top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.7);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .modal-overlay.show { display: flex; }
    .modal {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 32px;
      width: 520px;
      max-width: 90vw;
    }
    .modal h2 { font-size: 20px; margin-bottom: 20px; }
    .form-group { margin-bottom: 16px; }
    .form-label { font-size: 13px; color: var(--text-dim); margin-bottom: 6px; display: block; }
    .form-input, .form-textarea {
      width: 100%;
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 10px 12px;
      color: var(--text);
      font-family: inherit;
      font-size: 14px;
    }
    .form-input:focus, .form-textarea:focus { outline: none; border-color: var(--accent); }
    .form-textarea { min-height: 60px; resize: vertical; }
    .platform-chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .platform-chip {
      padding: 4px 12px;
      border-radius: 14px;
      border: 1px solid var(--border);
      font-size: 12px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .platform-chip.selected { background: var(--accent); border-color: var(--accent); color: white; }

    /* Loading */
    .loading { text-align: center; padding: 40px; color: var(--text-dim); }
    .spinner {
      width: 24px; height: 24px;
      border: 2px solid var(--border);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin: 0 auto 12px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* Discovery */
    .discovery-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 20px;
      border-bottom: 1px solid var(--border);
    }
    .discovery-item:last-child { border-bottom: none; }
    .discovery-keyword { font-size: 14px; font-weight: 500; }
    .discovery-meta { font-size: 12px; color: var(--text-dim); display: flex; gap: 12px; }

    /* Tabs */
    .tabs { display: flex; gap: 4px; margin-bottom: 20px; border-bottom: 1px solid var(--border); }
    .tab {
      padding: 8px 16px;
      font-size: 14px;
      color: var(--text-dim);
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all 0.15s;
    }
    .tab:hover { color: var(--text); }
    .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
    .tab-content { display: none; }
    .tab-content.active { display: block; }

    .empty-state { text-align: center; padding: 60px 20px; color: var(--text-dim); }
    .empty-state h3 { font-size: 18px; margin-bottom: 8px; color: var(--text); }
    .empty-state p { font-size: 14px; max-width: 400px; margin: 0 auto 20px; line-height: 1.6; }

    .toast {
      position: fixed; bottom: 24px; right: 24px;
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 12px 20px;
      font-size: 14px;
      z-index: 200;
      animation: slideIn 0.3s ease;
    }
    @keyframes slideIn { from { transform: translateY(100px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
  </style>
</head>
<body>
<div class="app">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-logo">Bounty<span>.</span></div>
    <div class="nav-item active" onclick="switchView('overview')">Overview</div>
    <div class="nav-item" onclick="switchView('zones')">Zones</div>
    <div class="nav-item" onclick="switchView('alerts')">Alerts</div>
    <div class="nav-item" onclick="switchView('discovery')">Discovery</div>
    <div style="position:absolute;bottom:20px;padding:0 24px;">
      <a href="/" style="font-size:12px;color:var(--text-dim);text-decoration:none;">&larr; Back to site</a>
    </div>
  </div>

  <!-- Main -->
  <div class="main">
    <!-- Overview -->
    <div id="view-overview" class="tab-content active">
      <div class="header">
        <h1>Overview</h1>
        <div class="header-actions">
          <button class="btn" onclick="switchView('discovery')">Discover Keywords</button>
          <button class="btn btn-primary" onclick="openCreateModal()">+ New Zone</button>
        </div>
      </div>
      <div class="stats-grid" id="stats-grid">
        <div class="stat-card"><div class="stat-label">Total Zones</div><div class="stat-value" id="stat-zones">-</div></div>
        <div class="stat-card"><div class="stat-label">Active</div><div class="stat-value" id="stat-active">-</div></div>
        <div class="stat-card"><div class="stat-label">Due Now</div><div class="stat-value" id="stat-due">-</div></div>
        <div class="stat-card"><div class="stat-label">Items Collected</div><div class="stat-value" id="stat-items">-</div></div>
      </div>
      <div class="section-title">Recent Alerts</div>
      <div id="overview-alerts"><div class="loading">Loading...</div></div>
    </div>

    <!-- Zones -->
    <div id="view-zones" class="tab-content">
      <div class="header">
        <h1>Monitoring Zones</h1>
        <button class="btn btn-primary" onclick="openCreateModal()">+ New Zone</button>
      </div>
      <div id="zones-list"><div class="loading"><div class="spinner"></div>Loading zones...</div></div>
    </div>

    <!-- Alerts -->
    <div id="view-alerts" class="tab-content">
      <div class="header"><h1>Trend Alerts</h1></div>
      <div id="alerts-list"><div class="loading">Loading...</div></div>
    </div>

    <!-- Discovery -->
    <div id="view-discovery" class="tab-content">
      <div class="header">
        <h1>Discovery</h1>
        <button class="btn btn-primary" onclick="runDiscovery()">Scan Now</button>
      </div>
      <p style="color:var(--text-dim);font-size:14px;margin-bottom:16px;">
        Candidate keywords from Google Trends, verified against real social discussion.
      </p>
      <div id="discovery-filters" style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;font-size:13px;align-items:center;">
        <label style="display:flex;align-items:center;gap:4px;">Min vol:
          <select id="df-minvol" style="background:var(--surface);border:1px solid var(--border);color:var(--text);padding:3px 6px;border-radius:4px;">
            <option value="0">Any</option>
            <option value="1000">1K+</option>
            <option value="10000">10K+</option>
            <option value="50000">50K+</option>
            <option value="100000">100K+</option>
          </select>
        </label>
        <label style="display:flex;align-items:center;gap:4px;">Min growth:
          <select id="df-mingrowth" style="background:var(--surface);border:1px solid var(--border);color:var(--text);padding:3px 6px;border-radius:4px;">
            <option value="0">Any</option>
            <option value="100">+100%</option>
            <option value="300">+300%</option>
            <option value="500">+500%</option>
          </select>
        </label>
        <label style="display:flex;align-items:center;gap:4px;">Freshness:
          <select id="df-age" style="background:var(--surface);border:1px solid var(--border);color:var(--text);padding:3px 6px;border-radius:4px;">
            <option value="0">Any time</option>
            <option value="3">Last 3h</option>
            <option value="6">Last 6h</option>
            <option value="12">Last 12h</option>
            <option value="24">Last 24h</option>
          </select>
        </label>
        <label style="display:flex;align-items:center;gap:4px;">
          <input type="checkbox" id="df-gateonly" style="accent-color:var(--accent);"> Verified only
        </label>
      </div>
      <div id="discovery-cats" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;font-size:12px;">
        <span style="color:var(--text-dim);margin-right:4px;">Categories:</span>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Business & Finance" style="accent-color:var(--accent);"> Business & Finance</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Consumer Products" style="accent-color:var(--accent);"> Consumer Products</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Entertainment" style="accent-color:var(--accent);"> Entertainment</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Food & Drink" style="accent-color:var(--accent);"> Food & Drink</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Gaming & Tech" style="accent-color:var(--accent);"> Gaming & Tech</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Health" style="accent-color:var(--accent);"> Health</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Hobbies & Pets" style="accent-color:var(--accent);"> Hobbies & Pets</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="News & Current Events" style="accent-color:var(--accent);"> News</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Politics & Government" style="accent-color:var(--accent);"> Politics</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Science" style="accent-color:var(--accent);"> Science</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Society & Culture" style="accent-color:var(--accent);"> Society</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Sports" style="accent-color:var(--accent);"> Sports</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Weather & Nature" style="accent-color:var(--accent);"> Weather</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Autos" style="accent-color:var(--accent);"> Autos</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Beauty & Fashion" style="accent-color:var(--accent);"> Beauty & Fashion</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Education" style="accent-color:var(--accent);"> Education</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Pets & Animals" style="accent-color:var(--accent);"> Pets</label>
        <label style="display:flex;align-items:center;gap:2px;cursor:pointer;"><input type="checkbox" class="df-cat" value="Travel" style="accent-color:var(--accent);"> Travel</label>
      </div>
      <div id="discovery-list"><div class="loading">Click "Scan Now" to discover emerging keywords.</div></div>
    </div>
  </div>
</div>

<!-- Create Zone Modal -->
<div class="modal-overlay" id="create-modal">
  <div class="modal">
    <h2>Create Monitoring Zone</h2>
    <div class="form-group">
      <label class="form-label">Zone Name</label>
      <input class="form-input" id="zone-name" placeholder="e.g. glp1-medications" />
    </div>
    <div class="form-group">
      <label class="form-label">Description (optional)</label>
      <input class="form-input" id="zone-desc" placeholder="What this zone monitors" />
    </div>
    <div class="form-group">
      <label class="form-label">Keywords (one per line)</label>
      <textarea class="form-textarea" id="zone-keywords" placeholder="ozempic weight loss&#10;wegovy results&#10;mounjaro review"></textarea>
    </div>
    <div class="form-group">
      <label class="form-label">Platforms</label>
      <div class="platform-chips" id="platform-chips">
        <div class="platform-chip selected" data-p="youtube">YouTube</div>
        <div class="platform-chip selected" data-p="reddit">Reddit</div>
        <div class="platform-chip selected" data-p="tiktok">TikTok</div>
        <div class="platform-chip selected" data-p="x">X</div>
        <div class="platform-chip selected" data-p="instagram">Instagram</div>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Collection Interval (hours)</label>
      <input class="form-input" id="zone-interval" type="number" value="168" style="width:120px" />
    </div>
    <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:24px;">
      <button class="btn" onclick="closeCreateModal()">Cancel</button>
      <button class="btn btn-primary" onclick="createZone()">Create Zone</button>
    </div>
  </div>
</div>

<!-- Zone Detail Modal -->
<div class="modal-overlay" id="detail-modal">
  <div class="modal" style="width:720px;max-height:85vh;overflow-y:auto;">
    <div id="zone-detail-content"></div>
    <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:24px;">
      <button class="btn" onclick="closeDetailModal()">Close</button>
    </div>
  </div>
</div>

<script>
const API = '/dashboard/api';
let selectedPlatforms = new Set(['youtube','reddit','tiktok','x','instagram']);

// ── Navigation ────────────────────────────
function switchView(view) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.getElementById('view-' + view).classList.add('active');
  event?.target?.classList?.add('active');

  if (view === 'overview') { loadStats(); loadAlerts('overview-alerts', 5); }
  if (view === 'zones') loadZones();
  if (view === 'alerts') loadAlerts('alerts-list', 50);
}

// ── Stats ─────────────────────────────────
async function loadStats() {
  try {
    const resp = await fetch(`${API}/stats`);
    const data = await resp.json();
    document.getElementById('stat-zones').textContent = data.total_zones;
    document.getElementById('stat-active').textContent = data.active_zones;
    document.getElementById('stat-due').textContent = data.zones_due;
    document.getElementById('stat-items').textContent = data.total_items_collected?.toLocaleString() || 0;
  } catch(e) { console.error('Stats error:', e); }
}

// ── Zones ─────────────────────────────────
async function loadZones() {
  try {
    const resp = await fetch(`${API}/zones`);
    const data = await resp.json();
    const list = document.getElementById('zones-list');

    if (!data.zones || data.zones.length === 0) {
      list.innerHTML = `<div class="empty-state">
        <h3>No zones yet</h3>
        <p>Create a monitoring zone to start collecting social intelligence across platforms.
        Each zone tracks 4-5 keywords and clusters posts to detect emerging trends.</p>
        <button class="btn btn-primary" onclick="openCreateModal()">+ Create Your First Zone</button>
      </div>`;
      return;
    }

    list.innerHTML = data.zones.map(z => `
      <div class="zone-card" id="zone-card-${z.id}">
        <div class="zone-header">
          <div>
            <div style="font-size:1.1rem;font-weight:600;color:var(--text-primary);">${z.name}</div>
            <div style="color:var(--text-muted);font-size:0.85rem;margin-top:2px;">${z.description || ''}</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;">
              ${(z.keywords||[]).map(k => `<span class="tag">${k}</span>`).join('')}
            </div>
            <div style="display:flex;gap:16px;margin-top:8px;color:var(--text-muted);font-size:0.8rem;">
              <span>${(z.platforms||[]).join(', ')}</span>
              <span>Every ${z.interval_hours}h</span>
              ${z.last_collected_at ? `<span>Last: ${formatDate(z.last_collected_at)}</span>` : '<span style="color:var(--yellow)">Never collected</span>'}
              <span style="color:var(--text-dim);">Auto-runs every ${z.interval_hours}h</span>
            </div>
          </div>
          <div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px;">
            <span class="badge badge-${z.status}">${z.status}</span>
            <div class="zone-actions">
              <button class="btn btn-sm btn-primary" id="run-btn-${z.id}" onclick="runZone(${z.id})">Run Now</button>
              <button class="btn btn-sm" onclick="viewZone(${z.id})">Details</button>
              ${z.status === 'active'
                ? `<button class="btn btn-sm" onclick="pauseZone(${z.id})">Pause</button>`
                : `<button class="btn btn-sm" onclick="resumeZone(${z.id})">Resume</button>`}
              <button class="btn btn-sm btn-danger" onclick="deleteZone(${z.id}, '${z.name}')">Delete</button>
            </div>
          </div>
        </div>
        <div id="zone-progress-${z.id}" style="display:none;"></div>
      </div>
    `).join('');

    // Check if any zones are currently running and resume polling
    data.zones.forEach(z => checkZoneStatus(z.id));
  } catch(e) {
    document.getElementById('zones-list').innerHTML = `<div class="loading">Error loading zones: ${e}</div>`;
  }
}

// ── Zone Actions ──────────────────────────

const _zonePollers = {};  // zone_id -> interval id

async function runZone(id) {
  const btn = document.getElementById(`run-btn-${id}`);
  if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }

  try {
    const resp = await fetch(`${API}/zones/${id}/run`, { method: 'POST' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const job = await resp.json();

    if (job.status === 'running') {
      showZoneProgress(id, job);
      startZonePolling(id);
    } else if (job.status === 'done' && job.result) {
      viewZoneReport(id, job.result);
    }
  } catch(e) {
    showToast(`Error: ${e}`);
    if (btn) { btn.disabled = false; btn.textContent = 'Run Now'; }
  }
}

function showZoneProgress(id, job) {
  const el = document.getElementById(`zone-progress-${id}`);
  if (!el) return;
  el.style.display = 'block';
  el.innerHTML = `
    <div style="margin-top:16px;padding:16px;background:var(--bg-darker);border-radius:8px;border:1px solid var(--border);">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
        <div class="spinner" style="width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite;"></div>
        <span style="color:var(--text-primary);font-weight:500;">${job.step || 'Running...'}</span>
      </div>
      <div style="width:100%;height:6px;background:var(--bg-darkest);border-radius:3px;overflow:hidden;">
        <div id="zone-bar-${id}" style="width:${job.progress||5}%;height:100%;background:var(--accent);border-radius:3px;transition:width 0.5s ease;"></div>
      </div>
      <div style="display:flex;justify-content:space-between;margin-top:6px;font-size:0.75rem;color:var(--text-muted);">
        <span>${job.progress||5}%</span>
        <span>You can navigate away - this runs in the background</span>
      </div>
    </div>
  `;
  const btn = document.getElementById(`run-btn-${id}`);
  if (btn) { btn.disabled = true; btn.textContent = 'Running...'; btn.style.opacity = '0.5'; }
}

function startZonePolling(id) {
  if (_zonePollers[id]) clearInterval(_zonePollers[id]);
  _zonePollers[id] = setInterval(async () => {
    try {
      const resp = await fetch(`${API}/zones/${id}/status`);
      const job = await resp.json();

      if (job.status === 'running') {
        // Update progress UI
        const bar = document.getElementById(`zone-bar-${id}`);
        if (bar) bar.style.width = `${job.progress||5}%`;
        const progEl = document.getElementById(`zone-progress-${id}`);
        if (progEl) {
          const stepSpan = progEl.querySelector('span');
          if (stepSpan && job.step) stepSpan.textContent = job.step;
        }
      } else if (job.status === 'done') {
        clearInterval(_zonePollers[id]);
        delete _zonePollers[id];
        hideZoneProgress(id);
        showToast(job.step || 'Zone collection complete');
        if (job.result) {
          viewZoneReport(id, job.result);
        }
        loadZones(); // refresh last-collected time
      } else if (job.status === 'error') {
        clearInterval(_zonePollers[id]);
        delete _zonePollers[id];
        hideZoneProgress(id);
        showToast(`Error: ${job.error || 'Zone run failed'}`);
      }
    } catch(e) { /* ignore poll errors */ }
  }, 2000);
}

function hideZoneProgress(id) {
  const el = document.getElementById(`zone-progress-${id}`);
  if (el) el.style.display = 'none';
  const btn = document.getElementById(`run-btn-${id}`);
  if (btn) { btn.disabled = false; btn.textContent = 'Run Now'; btn.style.opacity = '1'; }
}

async function checkZoneStatus(id) {
  // Called on page load to check if a zone was already running
  try {
    const resp = await fetch(`${API}/zones/${id}/status`);
    const job = await resp.json();
    if (job.status === 'running') {
      showZoneProgress(id, job);
      startZonePolling(id);
    }
  } catch(e) { /* ignore */ }
}

async function viewZone(id) {
  try {
    const resp = await fetch(`${API}/zones/${id}/report?limit=1`);
    const data = await resp.json();
    if (data.snapshots && data.snapshots.length > 0) {
      viewZoneReport(id, null, data.snapshots[0]);
    } else {
      showToast('No reports yet. Run the zone first.');
    }
  } catch(e) { showToast(`Error: ${e}`); }
}

function viewZoneReport(id, liveReport, snapshot) {
  const report = liveReport || {};
  const snap = snapshot || {};
  const clusters = liveReport?.top_clusters || snap?.clusters || [];
  const alerts = liveReport?.alerts || [];
  const enrichment = liveReport?.enrichment || {};

  let html = `<h2>Zone Report</h2>`;

  // Summary
  if (liveReport) {
    html += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px;">
      <div class="stat-card"><div class="stat-label">Items</div><div class="stat-value">${report.total_items}</div></div>
      <div class="stat-card"><div class="stat-label">Clusters</div><div class="stat-value">${report.cluster_count}</div></div>
      <div class="stat-card"><div class="stat-label">Alerts</div><div class="stat-value">${alerts.length}</div></div>
    </div>`;
    if (report.platform_summary) {
      html += `<div class="section-title">Platform Breakdown</div><div style="margin-bottom:20px;">`;
      for (const [p, s] of Object.entries(report.platform_summary)) {
        html += `<div class="discovery-item">
          <div class="discovery-keyword">${p}</div>
          <div class="discovery-meta">
            <span>${s.items} items</span>
            ${s.likes ? `<span>${s.likes.toLocaleString()} likes</span>` : ''}
            ${s.views ? `<span>${s.views.toLocaleString()} views</span>` : ''}
          </div>
        </div>`;
      }
      html += `</div>`;
    }
  }

  // Alerts
  if (alerts.length > 0) {
    html += `<div class="section-title">Alerts (${alerts.length})</div>`;
    alerts.forEach(a => {
      html += `<div class="alert-item">
        <div class="alert-type alert-${a.alert_type}">${a.alert_type}</div>
        <div class="alert-content">
          <div class="alert-label">${a.cluster_label}</div>
          <div class="alert-meta">
            ${a.zone_name} | ${a.previous_count} &rarr; ${a.current_count}
            ${a.growth_rate !== Infinity ? `(${a.growth_rate.toFixed(1)}x)` : ''}
            | ${a.platforms.join(', ')}
          </div>
        </div>
      </div>`;
    });
  }

  // Enrichment
  if (enrichment && enrichment.emotion_distribution) {
    html += `<div class="section-title">Enrichment Insights</div>`;
    const maxEmotion = Math.max(...Object.values(enrichment.emotion_distribution));
    html += `<div style="margin-bottom:16px;">`;
    for (const [emotion, count] of Object.entries(enrichment.emotion_distribution)) {
      const width = (count / maxEmotion * 200);
      html += `<div class="enrich-bar">
        <div class="enrich-label">${emotion}</div>
        <div class="enrich-fill" style="width:${width}px"></div>
        <div class="enrich-count">${count}</div>
      </div>`;
    }
    html += `</div>`;

    if (enrichment.top_pain_points?.length) {
      html += `<div style="margin-bottom:12px;"><strong style="font-size:13px;">Pain Points:</strong></div>`;
      enrichment.top_pain_points.forEach(p => { html += `<div style="font-size:13px;color:var(--text-dim);margin-bottom:4px;">&bull; ${p}</div>`; });
    }
    if (enrichment.top_brands?.length) {
      html += `<div style="margin:12px 0;"><strong style="font-size:13px;">Brands:</strong> ${enrichment.top_brands.join(', ')}</div>`;
    }
  }

  // Clusters
  if (clusters.length > 0) {
    html += `<div class="section-title">Top Clusters</div>`;
    clusters.slice(0, 10).forEach(c => {
      const cc = liveReport ? c : c; // both are dicts
      html += `<div class="cluster-card">
        <div class="cluster-header">
          <div class="cluster-label">${cc.label || 'unnamed'}</div>
          <div class="cluster-stats">
            <span>${cc.post_count || 0} posts</span>
            ${cc.total_likes ? `<span>${cc.total_likes.toLocaleString()} likes</span>` : ''}
            <span>${(cc.platforms || []).join(', ')}</span>
          </div>
        </div>`;
      if (cc.sample_posts) {
        html += `<div class="cluster-samples">`;
        cc.sample_posts.slice(0, 3).forEach(p => {
          html += `<div class="sample-post">
            <div class="sample-meta">@${p.author || '?'} | ${p.platform || '?'} ${p.likes ? '| ' + p.likes + ' likes' : ''}</div>
            ${p.text || ''}
          </div>`;
        });
        html += `</div>`;
      }
      html += `</div>`;
    });
  }

  document.getElementById('zone-detail-content').innerHTML = html;
  document.getElementById('detail-modal').classList.add('show');
}

async function deleteZone(id, name) {
  if (!confirm(`Delete zone "${name}"? This removes all collected data.`)) return;
  await fetch(`${API}/zones/${id}`, { method: 'DELETE' });
  loadZones();
  showToast('Zone deleted');
}

async function pauseZone(id) {
  await fetch(`${API}/zones/${id}/pause`, { method: 'POST' });
  loadZones();
}
async function resumeZone(id) {
  await fetch(`${API}/zones/${id}/resume`, { method: 'POST' });
  loadZones();
}

// ── Create Zone ───────────────────────────
function openCreateModal() {
  document.getElementById('create-modal').classList.add('show');
  document.getElementById('zone-name').focus();
}
function closeCreateModal() {
  document.getElementById('create-modal').classList.remove('show');
  document.getElementById('zone-name').value = '';
  document.getElementById('zone-desc').value = '';
  document.getElementById('zone-keywords').value = '';
}

document.querySelectorAll('.platform-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    chip.classList.toggle('selected');
    const p = chip.dataset.p;
    if (selectedPlatforms.has(p)) selectedPlatforms.delete(p);
    else selectedPlatforms.add(p);
  });
});

async function createZone() {
  const name = document.getElementById('zone-name').value.trim();
  const desc = document.getElementById('zone-desc').value.trim();
  const keywords = document.getElementById('zone-keywords').value
    .split('\\n').map(k => k.trim()).filter(k => k);
  const interval = parseInt(document.getElementById('zone-interval').value) || 168;

  if (!name || keywords.length === 0) {
    showToast('Name and at least one keyword required');
    return;
  }

  try {
    const resp = await fetch(`${API}/zones`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, description: desc, keywords,
        platforms: [...selectedPlatforms],
        interval_hours: interval,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      showToast(`Error: ${err.detail || 'Failed'}`);
      return;
    }
    closeCreateModal();
    loadZones();
    showToast(`Zone "${name}" created`);
  } catch(e) { showToast(`Error: ${e}`); }
}

// ── Alerts ────────────────────────────────
async function loadAlerts(elementId, limit) {
  try {
    const resp = await fetch(`${API}/alerts`);
    const data = await resp.json();
    const el = document.getElementById(elementId);
    const alerts = (data.alerts || []).slice(0, limit);

    if (alerts.length === 0) {
      el.innerHTML = '<div class="loading">No alerts yet. Run zones to generate trend alerts.</div>';
      return;
    }

    el.innerHTML = alerts.map(a => `
      <div class="alert-item">
        <div class="alert-type alert-${a.alert_type}">${a.alert_type}</div>
        <div class="alert-content">
          <div class="alert-label">${a.cluster_label}</div>
          <div class="alert-meta">
            ${a.zone_name} | ${a.previous_count} &rarr; ${a.current_count}
            ${a.growth_rate !== Infinity ? `(${a.growth_rate.toFixed(1)}x)` : ''}
            | ${(a.platforms || []).join(', ')}
          </div>
        </div>
      </div>
    `).join('');
  } catch(e) {
    document.getElementById(elementId).innerHTML = `<div class="loading">Error: ${e}</div>`;
  }
}

// ── Discovery ─────────────────────────────
function escapeDiscovery(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[ch]);
}

function safeEvidenceUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch (_) { return ''; }
}

async function runDiscovery() {
  const minVol = document.getElementById('df-minvol') ? document.getElementById('df-minvol').value : '0';
  const minGrowth = document.getElementById('df-mingrowth') ? document.getElementById('df-mingrowth').value : '0';
  const maxAge = document.getElementById('df-age') ? document.getElementById('df-age').value : '0';
  const gateOnly = document.getElementById('df-gateonly') ? document.getElementById('df-gateonly').checked : false;

  const selectedCats = Array.from(document.querySelectorAll('.df-cat:checked')).map(c => c.value);
  const catParam = selectedCats.length > 0 ? '&categories=' + encodeURIComponent(selectedCats.join(',')) : '';

  let qs = `geo=US&min_volume=${minVol}&min_growth=${minGrowth}&max_age_hours=${maxAge}${catParam}`;
  if (gateOnly) qs += '&gate_only=true';

  document.getElementById('discovery-list').innerHTML = '<div class="loading"><div class="spinner"></div>Fetching Google Trends + running conversation gate...</div>';
  try {
    const resp = await fetch(`${API}/discover?${qs}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const keywords = data.keywords || [];

    if (keywords.length === 0) {
      document.getElementById('discovery-list').innerHTML = '<div class="loading">No keywords discovered.</div>';
      return;
    }

    const supported = keywords.filter(k => k.conversation_analysis?.status === 'supported').length;
    const checked = keywords.filter(k => k.gate_status && k.gate_status !== 'not_checked').length;
    const unavailable = keywords.filter(k => ['partial', 'failed'].includes(k.gate_status)).length;

    document.getElementById('discovery-list').innerHTML = `
      <div style="margin-bottom:16px;font-size:13px;color:var(--text-dim);">
        ${data.total} Google Trends candidates &middot; ${checked} conversation checks &middot;
        ${supported} with citation-backed signals${unavailable ? ` &middot; ${unavailable} with incomplete sources` : ''}.
      </div>
      ${keywords.map((k, idx) => {
        const vol = k.search_volume != null ? (k.search_volume >= 1000 ? (k.search_volume/1000).toFixed(0)+'K' : k.search_volume) : 'unknown';
        const growth = k.growth_pct != null ? '+' + k.growth_pct + '%' : '';
        const fresh = k.started_hours_ago != null ? k.started_hours_ago.toFixed(1) + 'h ago' : '';
        const catLabel = k.categories ? '<span style="color:#9ca3af">' + escapeDiscovery(k.categories.split(',')[0]) + '</span>' : '';
        const analysis = k.conversation_analysis || {};
        const evidence = Object.fromEntries((analysis.evidence || []).map(item => [item.id, item]));
        const signalRows = (analysis.signals || []).slice(0, 3).map(signal => {
          const links = (signal.evidence_ids || []).map(id => {
            const item = evidence[id] || {};
            const url = safeEvidenceUrl(item.url);
            const label = escapeDiscovery((item.platform || id.split(':')[0]) + ' source');
            return url ? `<a href="${escapeDiscovery(url)}" target="_blank" rel="noopener">${label}</a>` : label;
          }).join(', ');
          return `<div style="margin-top:7px;padding-left:10px;border-left:2px solid #374151;">
            <span style="color:#93c5fd;font-family:'JetBrains Mono',monospace;font-size:10px;text-transform:uppercase;">${escapeDiscovery(signal.kind)}</span>
            <span style="color:var(--text);">${escapeDiscovery(signal.claim)}</span>
            <div style="font-size:10px;color:#6b7280;">${signal.independent_voices || 0} voices &middot; ${signal.thread_count || 0} threads &middot; ${links}</div>
          </div>`;
        }).join('');
        const coverage = analysis.coverage || {};
        const limitations = (analysis.limitations || []).slice(0, 2).map(item => escapeDiscovery(item)).join(' &middot; ');
        const convBlock = (analysis.summary || signalRows || limitations) ? `<div style="margin-top:7px;font-size:12px;color:var(--text-dim);line-height:1.5;">
          ${analysis.summary ? `<div style="color:var(--text);">${escapeDiscovery(analysis.summary)}</div>` : ''}
          ${signalRows}
          ${coverage.deduplicated_records != null ? `<div style="margin-top:7px;font-size:10px;color:#6b7280;">Coverage: ${coverage.deduplicated_records} records &middot; ${coverage.independent_voices || 0} voices &middot; ${coverage.platform_count || 0} platforms</div>` : ''}
          ${limitations ? `<div style="margin-top:4px;color:#f59e0b;font-size:10px;">Limits: ${limitations}</div>` : ''}
        </div>` : '';
        const gateLabels = {
          complete: '<span class="badge badge-active" style="margin-left:4px;background:#1a5f3f;">CHECK COMPLETE</span>',
          empty: '<span style="color:var(--text-dim);margin-left:4px;">checked: no records</span>',
          partial: '<span style="color:#f59e0b;margin-left:4px;">partial coverage</span>',
          failed: '<span style="color:#f87171;margin-left:4px;">sources unavailable</span>',
          not_checked: '<span style="color:#6b7280;margin-left:4px;">not checked</span>'
        };
        const gateBadge = gateLabels[k.gate_status] || gateLabels.not_checked;
        const gateSample = !convBlock && k.gate_sample ? '<div style="font-size:11px;color:var(--text-dim);margin-top:2px;font-style:italic;">' + escapeDiscovery(k.gate_sample.substring(0,100)) + '</div>' : '';
        const escaped = k.keyword.replace(/'/g, "\\'");
        return `
        <div class="discovery-item">
          <div>
            <div class="discovery-keyword">${escapeDiscovery(k.keyword)}</div>
            <div class="discovery-meta">
              <span>vol ${vol}</span>
              ${growth ? '<span style="color:#4ade80">' + growth + '</span>' : ''}
              ${fresh ? '<span>' + fresh + '</span>' : ''}
              ${catLabel}
              ${gateBadge}
            </div>
            ${gateSample}
            ${convBlock}
          </div>
          <div style="display:flex;align-items:center;gap:12px;">
            <button class="btn btn-sm" onclick="createZoneFromKeyword('${escaped}')">+ Zone</button>
          </div>
        </div>`;
      }).join('')}
    `;
  } catch(e) {
    document.getElementById('discovery-list').innerHTML = '<div class="loading">Error: ' + e + '</div>';
  }
}

function createZoneFromKeyword(keyword) {
  openCreateModal();
  document.getElementById('zone-name').value = keyword.toLowerCase().replace(/\\s+/g, '-').substring(0, 30);
  document.getElementById('zone-keywords').value = keyword;
}

// ── Utils ─────────────────────────────────
function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function showToast(msg) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}

function closeDetailModal() {
  document.getElementById('detail-modal').classList.remove('show');
}

// ── Init ──────────────────────────────────
loadStats();
loadAlerts('overview-alerts', 5);
</script>
</body>
</html>"""


def get_dashboard_html() -> str:
    return DASHBOARD_HTML
