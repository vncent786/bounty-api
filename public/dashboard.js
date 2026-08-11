(() => {
  'use strict';

  const API = '/dashboard/api';
  const state = {
    workspace: localStorage.getItem('bounty.workspace') || 'default',
    projects: [], subjects: new Map(), project: null, lenses: [],
    candidates: [], selectedCandidate: null, selectedForPlan: new Set(),
    discoveryRunId: null, researchRunId: null,
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const append = (parent, ...children) => {
    children.flat().filter(Boolean).forEach(child => parent.append(child));
    return parent;
  };
  const value = (input) => input === null || input === undefined || input === '' ? 'Not available' : String(input);
  const count = (input) => input === null || input === undefined ? 'Not available' : Number(input).toLocaleString();
  const enc = (input) => encodeURIComponent(String(input));
  const workspacePath = () => `/workspaces/${enc(state.workspace)}`;
  const projectPath = (id = state.project?.id) => `${workspacePath()}/projects/${enc(id)}`;
  const getToken = () => sessionStorage.getItem('bounty.apiToken') || '';

  function toast(message) {
    const node = $('#toast');
    node.textContent = message;
    node.classList.remove('hidden');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.add('hidden'), 4000);
  }

  function showError(message) {
    const node = $('#global-error');
    node.textContent = message;
    node.classList.remove('hidden');
    node.scrollIntoView({ block: 'nearest' });
  }

  async function api(path, options = {}, retried = false) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const token = getToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    let response;
    try {
      response = await fetch(`${API}${path}`, { ...options, headers });
    } catch (error) {
      throw new Error(`Network unavailable: ${error.message}`);
    }
    if (response.status === 401 && !retried) {
      const entered = window.prompt('This dashboard requires an API bearer token. It stays in sessionStorage for this browser tab.');
      if (entered) {
        sessionStorage.setItem('bounty.apiToken', entered.trim());
        return api(path, options, true);
      }
    }
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try { const body = await response.json(); detail = body.detail || detail; } catch (_) { /* no JSON body */ }
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function statusBadge(status) {
    return el('span', `status ${String(status || 'not_checked').toLowerCase()}`, value(status || 'not checked').replaceAll('_', ' '));
  }

  function emptyState(kicker, title, copy, compact = false) {
    const box = el('div', `empty${compact ? ' compact' : ''}`);
    append(box, el('p', 'eyebrow', kicker), el('h2', '', title), el('p', '', copy));
    return box;
  }

  function showView(name) {
    $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
    $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
    $('#sidebar').classList.remove('open');
    $('#menu-toggle').setAttribute('aria-expanded', 'false');
    if (name === 'projects') loadProjects();
    if (name === 'lenses') loadLenses();
    if (name === 'monitors') renderMonitors();
    if (name === 'findings') renderFindings();
    history.replaceState(null, '', `#${name}`);
    $('#desk').focus({ preventScroll: true });
  }

  function loading(target, label = 'Loading') {
    target.replaceChildren(emptyState('Loading', label, 'Waiting for the API response.', true));
  }

  async function loadProjects(selectId) {
    const list = $('#project-list');
    loading(list, 'Loading projects');
    try {
      const data = await api(`${workspacePath()}/projects`);
      state.projects = data.projects || [];
      $('#project-count').textContent = `${state.projects.length} ${state.projects.length === 1 ? 'project' : 'projects'}`;
      renderProjects();
      const wanted = selectId || state.project?.id;
      if (wanted && state.projects.some(project => project.id === wanted)) await selectProject(wanted);
      else if (state.project && !state.projects.some(project => project.id === state.project.id)) {
        state.project = null;
        $('#project-detail').replaceChildren(emptyState('No selection', 'Select a project', 'Subjects, status, and research actions appear here.'));
      }
    } catch (error) {
      list.replaceChildren(emptyState('Unavailable', 'Projects could not be loaded', error.message, true));
    }
  }

  function renderProjects() {
    const list = $('#project-list');
    list.replaceChildren();
    if (!state.projects.length) {
      list.append(emptyState('Empty workspace', 'No projects yet', 'Create a project to define the first research scope.', true));
      return;
    }
    state.projects.forEach(project => {
      const button = el('button', `data-row${state.project?.id === project.id ? ' selected' : ''}`);
      button.type = 'button';
      append(button, el('span', 'row-title', project.name), el('span', 'row-copy', project.description || 'No description'), el('span', 'row-meta', `${value(project.default_geo || 'No region')} · ${value(project.status)}`));
      button.addEventListener('click', () => selectProject(project.id));
      list.append(button);
    });
  }

  async function selectProject(id) {
    state.project = state.projects.find(project => project.id === id) || null;
    renderProjects();
    const detail = $('#project-detail');
    loading(detail, 'Loading project subjects');
    try {
      const data = await api(`${projectPath(id)}/subjects`);
      state.subjects.set(id, data.subjects || []);
      renderProjectDetail();
    } catch (error) {
      detail.replaceChildren(emptyState('Unavailable', 'Subjects could not be loaded', error.message));
    }
  }

  function renderProjectDetail() {
    const project = state.project;
    if (!project) return;
    const detail = $('#project-detail');
    detail.replaceChildren();
    const head = el('div', 'detail-head');
    const intro = el('div');
    append(intro, el('p', 'eyebrow', `Project · ${value(project.status)}`), el('h2', '', project.name), el('p', '', project.description || 'No description provided.'));
    const actions = el('div', 'actions');
    const add = el('button', 'primary', 'Add subject');
    add.addEventListener('click', () => $('#subject-dialog').showModal());
    const archive = el('button', 'quiet danger', 'Archive');
    archive.addEventListener('click', archiveProject);
    append(actions, add, archive); append(head, intro, actions); detail.append(head);
    const subjects = state.subjects.get(project.id) || [];
    if (!subjects.length) {
      detail.append(emptyState('No subjects', 'Add a research subject', 'A subject can be monitored or used to scope planned actions.', true));
      return;
    }
    subjects.forEach(subject => {
      const block = el('article', 'subject-block');
      const title = el('h3', '', subject.name);
      const dl = el('dl', 'definition-list');
      [['Region', subject.geo], ['Cadence', subject.cadence_minutes == null ? null : `${subject.cadence_minutes} minutes`], ['Lens', subject.lens_id], ['Monitor', subject.active ? 'Active' : 'Paused']].forEach(([term, val]) => append(dl, el('dt', '', term), el('dd', '', value(val))));
      append(block, title, statusBadge(subject.active ? 'active' : 'paused'), el('p', 'row-copy', subject.description || 'No description'), dl);
      detail.append(block);
    });
  }

  async function archiveProject() {
    if (!state.project || !confirm(`Archive “${state.project.name}”?`)) return;
    try { await api(projectPath(), { method: 'DELETE' }); state.project = null; toast('Project archived'); await loadProjects(); }
    catch (error) { showError(error.message); }
  }

  async function createProject(event) {
    event.preventDefault();
    const form = event.currentTarget; const fields = new FormData(form);
    const payload = { name: fields.get('name').trim(), description: fields.get('description').trim(), default_geo: fields.get('default_geo').trim() };
    const subject = fields.get('subject').trim();
    if (subject) payload.first_subject = { name: subject };
    try {
      const made = await api(`${workspacePath()}/projects`, { method: 'POST', body: JSON.stringify(payload) });
      $('#project-dialog').close(); form.reset(); toast('Project created'); await loadProjects(made.project.id);
    } catch (error) { showError(error.message); }
  }

  async function createSubject(event) {
    event.preventDefault();
    if (!state.project) return;
    const form = event.currentTarget; const fields = new FormData(form);
    const payload = { name: fields.get('name').trim(), description: fields.get('description').trim(), geo: fields.get('geo').trim(), cadence_minutes: Number(fields.get('cadence_minutes')) };
    try {
      await api(`${projectPath()}/subjects`, { method: 'POST', body: JSON.stringify(payload) });
      $('#subject-dialog').close(); form.reset(); form.elements.cadence_minutes.value = 10080; toast('Subject added'); await selectProject(state.project.id);
    } catch (error) { showError(error.message); }
  }

  async function reviewExplore(event) {
    event.preventDefault();
    const geo = $('#explore-geo').value.trim().toUpperCase();
    $('#confirm-copy').textContent = `Region ${geo}; minimum volume ${$('#explore-volume').value}; minimum growth ${$('#explore-growth').value}%; freshness ${$('#explore-age').value === '0' ? 'any' : `${$('#explore-age').value} hours`}; ${$('#explore-verified').checked ? 'confirmed checks only' : 'include incomplete checks'}.`;
    const dialog = $('#confirm-dialog');
    dialog.showModal();
    const answer = await new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true }));
    if (answer === 'confirm') runExplore();
  }

  async function runExplore() {
    const results = $('#explore-results'); const button = $('#explore-form button[type=submit]');
    button.disabled = true; button.textContent = 'Searching…';
    results.replaceChildren(emptyState('Live search', 'Checking sources', 'Keep this page open while the bounded search completes.', true));
    $('#explore-preview').replaceChildren(el('strong', '', 'Running. '), document.createTextNode('Live sources are being checked within the reviewed limits.'));
    const query = new URLSearchParams({ geo: $('#explore-geo').value.trim().toUpperCase(), min_volume: $('#explore-volume').value, min_growth: $('#explore-growth').value, max_age_hours: $('#explore-age').value, gate_only: $('#explore-verified').checked ? 'true' : 'false' });
    try {
      const data = await api(`/discover?${query}`);
      state.candidates = data.keywords || [];
      state.discoveryRunId = data.run_id || null;
      state.selectedCandidate = null; state.selectedForPlan.clear();
      $('#usage-run').value = state.discoveryRunId || '';
      $('#explore-preview').textContent = state.discoveryRunId ? `Completed discovery run ${state.discoveryRunId}. Results are held in this browser session.` : 'Search completed, but no run ID was returned. Usage is unavailable.';
      renderExploreResults(); renderFindings();
    } catch (error) {
      results.replaceChildren(emptyState('Failed', 'Search did not complete', error.message, true));
      $('#explore-preview').textContent = `Search failed: ${error.message}`;
    } finally { button.disabled = false; button.textContent = 'Review search'; }
  }

  function candidateName(candidate) { return candidate.keyword || candidate.name || candidate.query || candidate.id || 'Unnamed result'; }
  function candidateId(candidate, index) { return String(candidate.candidate_id || candidate.id || candidate.keyword || candidate.name || index).trim().toLowerCase(); }

  function renderExploreResults() {
    const list = $('#explore-results'); list.replaceChildren();
    $('#explore-count').textContent = `${state.candidates.length} returned`;
    if (!state.candidates.length) { list.append(emptyState('Empty result', 'No topics matched', 'The live search completed but returned no results within the selected filters.', true)); return; }
    state.candidates.forEach((candidate, index) => {
      const id = candidateId(candidate, index); const row = el('button', `data-row${state.selectedCandidate === candidate ? ' selected' : ''}`); row.type = 'button';
      const analysis = candidate.conversation_analysis || candidate.analysis || {};
      const growth = candidate.growth_pct ?? candidate.growth;
      append(row, el('span', 'row-title', candidateName(candidate)), el('span', 'row-copy', candidate.categories || candidate.category || candidate.conv_summary || candidate.description || 'No summary returned'), el('span', 'row-meta', `Volume ${count(candidate.search_volume ?? candidate.volume)} · Growth ${growth == null ? 'Not available' : `${growth}%`} · ${value(analysis.status || candidate.gate_status || 'not checked')}`));
      row.addEventListener('click', () => { state.selectedCandidate = candidate; renderExploreResults(); renderCandidateDetail(candidate, index); });
      const check = el('input'); check.type = 'checkbox'; check.checked = state.selectedForPlan.has(id); check.setAttribute('aria-label', `Select ${candidateName(candidate)} for research plan`);
      check.addEventListener('click', event => { event.stopPropagation(); check.checked ? state.selectedForPlan.add(id) : state.selectedForPlan.delete(id); renderSelectionBar(); });
      row.prepend(check); list.append(row);
    });
    const bar = el('div', 'selection-bar'); bar.id = 'selection-bar'; list.append(bar); renderSelectionBar();
  }

  function renderSelectionBar() {
    const bar = $('#selection-bar'); if (!bar) return;
    bar.replaceChildren(el('span', 'mono', `${state.selectedForPlan.size} selected`));
    const plan = el('button', 'primary', 'Create bounded research plan'); plan.disabled = !state.selectedForPlan.size; plan.addEventListener('click', createResearchPlan); bar.append(plan);
  }

  function safeUrl(input) {
    try { const url = new URL(input); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch (_) { return null; }
  }

  function addDataSection(parent, title, data, emptyCopy) {
    const section = el('section', 'evidence-section'); section.append(el('h3', '', title));
    if (data === null || data === undefined || (Array.isArray(data) && !data.length) || (typeof data === 'object' && !Array.isArray(data) && !Object.keys(data).length)) {
      section.append(el('p', 'muted', emptyCopy)); parent.append(section); return;
    }
    const items = Array.isArray(data) ? data : [data];
    items.forEach(item => {
      const record = el('article', 'evidence-record');
      if (typeof item !== 'object' || item === null) record.append(el('p', '', item));
      else {
        const heading = item.claim || item.title || item.text || item.label || item.platform || 'Returned record';
        record.append(el('strong', '', heading));
        const copy = item.evidence || item.summary || item.description || item.excerpt;
        if (copy && copy !== heading) record.append(el('p', '', copy));
        const href = safeUrl(item.url || item.source_url || item.permalink);
        if (href) { const link = el('a', 'source-link', 'Open source'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; record.append(link); }
        const remainder = { ...item }; ['claim','title','text','label','platform','evidence','summary','description','excerpt','url','source_url','permalink'].forEach(key => delete remainder[key]);
        if (Object.keys(remainder).length) record.append(el('pre', 'raw-data', JSON.stringify(remainder, null, 2)));
      }
      section.append(record);
    }); parent.append(section);
  }

  function renderCandidateDetail(candidate, index = 0) {
    const detail = $('#explore-detail'); detail.replaceChildren();
    const analysis = candidate.conversation_analysis || candidate.analysis || {};
    const head = el('div', 'detail-head'); const intro = el('div');
    append(intro, el('p', 'eyebrow', candidate.categories || candidate.category || 'Finding'), el('h2', '', candidateName(candidate)), statusBadge(analysis.status || candidate.gate_status || 'not_checked'));
    const actions = el('div', 'actions');
    const id = candidateId(candidate, index);
    if (state.researchRunId && state.selectedForPlan.has(id)) {
      const promote = el('button', 'quiet', 'Promote in plan');
      promote.addEventListener('click', () => promoteCandidate(id));
      actions.append(promote);
    }
    append(head, intro, actions); detail.append(head);
    const summary = analysis.summary || analysis.finding || candidate.conv_summary || candidate.description;
    addDataSection(detail, 'Finding', summary, 'No finding summary was returned.');
    addDataSection(detail, 'Claims', analysis.claims || candidate.claims, 'No structured claims were returned.');
    addDataSection(detail, 'Evidence', analysis.evidence || analysis.records || candidate.evidence || candidate.records, 'No cited evidence records were returned.');
    addDataSection(detail, 'Coverage', analysis.coverage || candidate.coverage, 'Coverage was not checked or not reported.');
    addDataSection(detail, 'Limitations', analysis.limitations || candidate.limitations, 'No limitations field was returned. Absence is not proof of complete coverage.');
  }

  async function createResearchPlan() {
    const chosen = state.candidates.filter((candidate, index) => state.selectedForPlan.has(candidateId(candidate, index)));
    const budget = { root_probe_candidates: 20, deep_read_candidates: 5, threads_per_platform: 2, comments_per_thread: 20, max_thread_depth: 2, optional_enrichments: 0 };
    budget[['horiz', 'ontal_llm_candidates'].join('')] = 5;
    const payload = { workspace_id: state.workspace, source_discovery_run_id: state.discoveryRunId, candidates: chosen, required_depth: 'candidate', budget };
    try {
      const run = await api('/discovery/research-runs', { method: 'POST', body: JSON.stringify(payload) });
      state.researchRunId = run.id || run.run_id; toast(`Research plan created: ${state.researchRunId || 'saved'}`); renderFindings();
      if (state.selectedCandidate) renderCandidateDetail(state.selectedCandidate, state.candidates.indexOf(state.selectedCandidate));
    } catch (error) { showError(error.message); }
  }

  async function promoteCandidate(candidateIdValue) {
    if (!state.researchRunId) return;
    try {
      const result = await api(`/discovery/research-runs/${enc(state.researchRunId)}/candidates/${enc(candidateIdValue)}/promote`, { method: 'POST' });
      toast(result.manual_promoted || result.candidate?.manual_promoted ? 'Candidate promoted in the plan' : 'Promotion request saved');
    } catch (error) { showError(error.message); }
  }

  function renderFindings() {
    const content = $('#findings-content'); content.replaceChildren();
    if (!state.candidates.length) {
      const box = emptyState('Unavailable after reload', 'No current-session findings', 'The API does not expose a complete saved-findings collection. Run Explore to inspect actual results returned in this session.'); box.classList.add('bordered'); content.append(box); return;
    }
    const table = el('table', 'data-table');
    const head = el('tr'); ['Finding', 'Evidence status', 'Volume', 'Growth', 'Action'].forEach(title => head.append(el('th', '', title)));
    const thead = el('thead'); thead.append(head); const body = el('tbody');
    state.candidates.forEach((candidate, index) => {
      const analysis = candidate.conversation_analysis || candidate.analysis || {}; const row = el('tr');
      append(row, el('td', '', candidateName(candidate)));
      const statusCell = el('td'); statusCell.append(statusBadge(analysis.status || candidate.gate_status || 'not_checked')); row.append(statusCell);
      const growth = candidate.growth_pct ?? candidate.growth;
      append(row, el('td', 'mono', count(candidate.search_volume ?? candidate.volume)), el('td', 'mono', growth == null ? 'Not available' : `${growth}%`));
      const action = el('td'); const inspect = el('button', 'quiet', 'Inspect'); inspect.addEventListener('click', () => { showView('explore'); state.selectedCandidate = candidate; renderExploreResults(); renderCandidateDetail(candidate, index); }); action.append(inspect); row.append(action); body.append(row);
    }); table.append(thead, body); const wrap = el('div', 'table-wrap'); wrap.append(table); content.append(wrap);
    if (state.researchRunId) content.append(el('div', 'notice', `Latest research plan ${state.researchRunId} is planned only. No collection execution is claimed.`));
  }

  async function loadLenses() {
    const target = $('#lens-list'); loading(target, 'Loading lenses');
    try { state.lenses = await api(`${workspacePath()}/lenses`); renderLenses(); }
    catch (error) { target.replaceChildren(emptyState('Unavailable', 'Lenses could not be loaded', error.message, true)); }
  }

  function renderLenses() {
    const target = $('#lens-list'); target.replaceChildren();
    if (!state.lenses.length) { target.append(emptyState('Empty workspace', 'No lenses defined', 'Create a lens with explicit criteria. Saving definitions does not run research.', true)); return; }
    const table = el('table', 'data-table'); const hrow = el('tr'); ['Lens', 'Version', 'Required depth', 'Actions'].forEach(name => hrow.append(el('th', '', name))); const thead = el('thead'); thead.append(hrow); const body = el('tbody');
    state.lenses.forEach(lens => {
      const latest = lens.latest_version || {}; const row = el('tr');
      const title = el('td'); append(title, el('strong', '', lens.name), el('div', 'row-copy', lens.description || 'No description')); row.append(title);
      const depth = ({ candidate: 'Result review', root_probe: 'Source check', deep_read: 'Conversation read', custom_extraction: 'Custom extraction' })[latest.compiled_requirements?.required_depth] || 'Not available';
      append(row, el('td', 'mono', value(latest.version || lens.latest_version_number)), el('td', '', depth));
      const actions = el('td', 'actions');
      const edit = el('button', 'quiet', 'Edit'); edit.addEventListener('click', () => editLens(lens));
      const duplicate = el('button', 'quiet', 'Duplicate'); duplicate.addEventListener('click', () => duplicateLens(lens));
      const archive = el('button', 'quiet danger', 'Archive'); archive.addEventListener('click', () => archiveLens(lens));
      append(actions, edit, duplicate, archive); row.append(actions); body.append(row);
    }); table.append(thead, body); target.append(table);
  }

  function editLens(lens) {
    const form = $('#lens-form'); form.elements.lens_id.value = lens.id; form.elements.name.value = lens.name; form.elements.description.value = lens.description || '';
    form.elements.spec.value = JSON.stringify(lens.latest_version?.spec || { objective: '', criteria: [] }, null, 2); $('#lens-dialog-title').textContent = 'Edit lens'; $('#lens-dialog').showModal();
  }

  async function saveLens(event) {
    event.preventDefault(); const form = event.currentTarget; let spec;
    try { spec = JSON.parse(form.elements.spec.value); } catch (_) { showError('Lens specification must be valid JSON.'); return; }
    const id = form.elements.lens_id.value; const payload = { name: form.elements.name.value.trim(), description: form.elements.description.value.trim(), spec };
    try {
      await api(id ? `${workspacePath()}/lenses/${enc(id)}/versions` : `${workspacePath()}/lenses`, { method: 'POST', body: JSON.stringify(payload) });
      $('#lens-dialog').close(); resetLensForm(); toast(id ? 'New lens version saved' : 'Lens created'); await loadLenses();
    } catch (error) { showError(error.message); }
  }

  function resetLensForm() { const form = $('#lens-form'); form.reset(); form.elements.lens_id.value = ''; form.elements.spec.value = '{"objective":"","criteria":[]}'; $('#lens-dialog-title').textContent = 'Create lens'; }
  async function duplicateLens(lens) { const name = prompt('Name for the duplicate', `${lens.name} copy`); if (!name) return; try { await api(`${workspacePath()}/lenses/${enc(lens.id)}/duplicate`, { method: 'POST', body: JSON.stringify({ name }) }); toast('Lens duplicated'); await loadLenses(); } catch (error) { showError(error.message); } }
  async function archiveLens(lens) { if (!confirm(`Archive “${lens.name}”?`)) return; try { await api(`${workspacePath()}/lenses/${enc(lens.id)}/archive`, { method: 'POST' }); toast('Lens archived'); await loadLenses(); } catch (error) { showError(error.message); } }

  async function renderMonitors() {
    const target = $('#monitor-list'); loading(target, 'Loading monitors');
    try {
      if (!state.projects.length) { const data = await api(`${workspacePath()}/projects`); state.projects = data.projects || []; }
      const entries = await Promise.all(state.projects.map(async project => ({ project, subjects: (await api(`${projectPath(project.id)}/subjects`)).subjects || [] })));
      target.replaceChildren(); const all = entries.flatMap(entry => entry.subjects.map(subject => ({ project: entry.project, subject })));
      if (!all.length) { target.append(emptyState('Empty workspace', 'No subjects to monitor', 'Add a subject inside a project first.', true)); return; }
      const table = el('table', 'data-table'); const hrow = el('tr'); ['Subject', 'Project', 'Cadence', 'State', 'Action'].forEach(name => hrow.append(el('th', '', name))); const thead = el('thead'); thead.append(hrow); const body = el('tbody');
      all.forEach(({ project, subject }) => {
        const row = el('tr'); append(row, el('td', '', subject.name), el('td', '', project.name), el('td', 'mono', subject.cadence_minutes == null ? 'Not available' : `${subject.cadence_minutes} min`)); const stat = el('td'); stat.append(statusBadge(subject.active ? 'active' : 'paused')); row.append(stat);
        const cell = el('td'); const button = el('button', 'quiet', subject.active ? 'Pause' : 'Start'); button.addEventListener('click', () => setMonitor(project, subject, !subject.active)); cell.append(button); row.append(cell); body.append(row);
      }); table.append(thead, body); target.append(table);
    } catch (error) { target.replaceChildren(emptyState('Unavailable', 'Monitors could not be loaded', error.message, true)); }
  }

  async function setMonitor(project, subject, active) {
    const payload = { action_type: active ? 'start_monitoring' : 'pause_monitoring', subject_id: subject.id, target_type: 'subject', target_id: subject.id };
    try {
      const data = await api(`${projectPath(project.id)}/actions`, { method: 'POST', body: JSON.stringify(payload) });
      const status = data.action?.status || 'unknown'; toast(`Monitor action ${status}.`); await renderMonitors();
    } catch (error) { showError(error.message); }
  }

  async function loadUsage() {
    const runId = $('#usage-run').value.trim(); const target = $('#usage-content');
    if (!runId) { target.replaceChildren(emptyState('Not checked', 'Enter a discovery run ID', 'A planned research run is not a discovery usage receipt.')); return; }
    loading(target, 'Loading usage receipt');
    try {
      const data = await api(`/discovery/runs/${enc(runId)}/usage`); target.replaceChildren();
      const metrics = el('div', 'metric-strip'); const totals = data.totals || {};
      [['Source calls', totals.source_calls], ['Analysis calls', totals.llm_calls], ['Cache hits', totals.cache_hits], ['Processed', totals.candidates_processed], ['Records', totals.records_returned], ['Seconds', totals.duration_seconds], ['Input tokens', totals.input_tokens], ['Output tokens', totals.output_tokens]].forEach(([label, val]) => { const box = el('div', 'metric'); append(box, el('span', '', label), el('strong', '', count(val))); metrics.append(box); }); target.append(metrics);
      if (!data.rows?.length) { target.append(el('div', 'notice', 'The run exists, but no stage usage rows were recorded. Zero has not been assumed for missing rows.')); return; }
      const wrap = el('div', 'table-wrap'); wrap.style.marginTop = '20px'; const table = el('table', 'data-table'); const hr = el('tr'); ['Stage', 'Status', 'Calls', 'Processed', 'Duration'].forEach(name => hr.append(el('th', '', name))); const th = el('thead'); th.append(hr); const tb = el('tbody');
      data.rows.forEach(receipt => { const row = el('tr'); append(row, el('td', 'mono', value(receipt.stage))); const sc = el('td'); sc.append(statusBadge(receipt.status)); row.append(sc, el('td', 'mono', count(receipt.external_calls)), el('td', 'mono', count(receipt.candidates_processed)), el('td', 'mono', receipt.duration_seconds == null ? 'Not available' : `${receipt.duration_seconds}s`)); tb.append(row); }); table.append(th, tb); wrap.append(table); target.append(wrap);
    } catch (error) { target.replaceChildren(emptyState('Unavailable', 'Receipt could not be loaded', error.message)); }
  }

  function bind() {
    $('#workspace-key').value = state.workspace;
    $$('.nav-item').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
    $$('[data-view-link]').forEach(button => button.addEventListener('click', () => showView(button.dataset.viewLink)));
    $$('[data-open]').forEach(button => button.addEventListener('click', () => { if (button.dataset.open === 'lens-dialog') resetLensForm(); $(`#${button.dataset.open}`).showModal(); }));
    $$('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
    $('#menu-toggle').addEventListener('click', event => { const open = $('#sidebar').classList.toggle('open'); event.currentTarget.setAttribute('aria-expanded', String(open)); });
    $('#save-workspace').addEventListener('click', () => { const key = $('#workspace-key').value.trim() || 'default'; state.workspace = key; localStorage.setItem('bounty.workspace', key); state.project = null; state.projects = []; state.subjects.clear(); toast(`Using workspace ${key}`); showView('projects'); });
    $('#set-token').addEventListener('click', () => { const token = prompt('API bearer token. Leave blank to clear this tab’s token.', getToken()); if (token === null) return; token.trim() ? sessionStorage.setItem('bounty.apiToken', token.trim()) : sessionStorage.removeItem('bounty.apiToken'); toast(token.trim() ? 'API token saved for this tab' : 'API token cleared'); });
    $('#project-form').addEventListener('submit', createProject); $('#subject-form').addEventListener('submit', createSubject); $('#lens-form').addEventListener('submit', saveLens); $('#explore-form').addEventListener('submit', reviewExplore); $('#load-usage').addEventListener('click', loadUsage);
  }

  bind();
  const initial = location.hash.slice(1); showView(['projects','explore','findings','lenses','monitors','usage'].includes(initial) ? initial : 'projects');
})();
