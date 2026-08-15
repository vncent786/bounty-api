(() => {
  'use strict';

  const API = '/dashboard/api';
  const state = {
    workspace: localStorage.getItem('bounty.workspace') || 'default',
    projects: [], subjects: new Map(), project: null, lenses: [],
    families: [], selectedFamily: null,
    candidates: [], selectedCandidate: null, selectedForPlan: new Set(),
    discoveryRunId: null, discoveryRunStatus: null, researchRunId: null, researchRunStatus: null,
    workspaceEpoch: 0, globalExploreEpoch: 0,
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
  const fmtDate = (input) => {
    if (!input) return 'Unknown';
    const parsed = new Date(input);
    if (Number.isNaN(parsed.getTime())) return 'Unknown';
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(parsed);
  };
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
      try {
        const body = await response.json();
        detail = typeof body.detail === 'object'
          ? [body.detail.message, body.detail.error_category].filter(Boolean).join(': ')
          : body.detail || detail;
      } catch (_) { /* no JSON body */ }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
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
    $$('.nav-item').forEach(item => {
      const active = item.dataset.view === name;
      item.classList.toggle('active', active);
      active ? item.setAttribute('aria-current', 'page') : item.removeAttribute('aria-current');
    });
    $('#sidebar').classList.remove('open');
    $('#menu-toggle').setAttribute('aria-expanded', 'false');
    if (name === 'projects') loadProjects();
    if (name === 'lenses') loadLenses();
    if (name === 'explore') {
      const epoch = state.workspaceEpoch;
      Promise.all([ensureLenses(), loadGlobalExplore()]).catch(error => {
        if (epoch === state.workspaceEpoch) showError(error.message);
      });
    }
    if (name === 'monitors') renderMonitors();
    if (name === 'findings') loadResearchHistory();
    history.replaceState(null, '', `#${name}`);
    $('#desk').focus({ preventScroll: true });
  }

  function loading(target, label = 'Loading') {
    target.replaceChildren(emptyState('Loading', label, 'Waiting for the API response.', true));
  }

  async function loadProjects(selectId) {
    const list = $('#project-list');
    const epoch = state.workspaceEpoch;
    loading(list, 'Loading projects');
    try {
      const data = await api(`${workspacePath()}/projects`);
      if (epoch !== state.workspaceEpoch) return;
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
      if (epoch === state.workspaceEpoch) list.replaceChildren(emptyState('Unavailable', 'Projects could not be loaded', error.message, true));
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
    const epoch = state.workspaceEpoch;
    loading(detail, 'Loading project subjects');
    try {
      const data = await api(`${projectPath(id)}/subjects`);
      if (epoch !== state.workspaceEpoch || state.project?.id !== id) return;
      state.subjects.set(id, data.subjects || []);
      renderProjectDetail();
    } catch (error) {
      if (epoch === state.workspaceEpoch && state.project?.id === id) detail.replaceChildren(emptyState('Unavailable', 'Subjects could not be loaded', error.message));
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
    add.addEventListener('click', async () => { try { await ensureLenses(); $('#subject-dialog').showModal(); } catch (error) { showError(error.message); } });
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
    const lensId = fields.get('lens_id');
    const lens = state.lenses.find(item => item.id === lensId);
    const payload = { name: fields.get('name').trim(), description: fields.get('description').trim(), geo: fields.get('geo').trim(), cadence_minutes: Number(fields.get('cadence_minutes')), lens_id: lensId || null, lens_version: lens ? Number(lens.latest_version?.version || lens.latest_version_number) : null };
    try {
      await api(`${projectPath()}/subjects`, { method: 'POST', body: JSON.stringify(payload) });
      $('#subject-dialog').close(); form.reset(); form.elements.cadence_minutes.value = 10080; toast('Subject added'); await selectProject(state.project.id);
    } catch (error) { showError(error.message); }
  }

  // ── Saved research briefs ──────────────────────────────────
  const terminalResearchStatuses = new Set(['complete', 'partial', 'error', 'cancelled']);
  const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const latestRunKey = () => `bounty.latestResearchRun.${state.workspace}`;

  function briefTopics() {
    const seen = new Set();
    return $('#direct-topic').value.split(/\r?\n/).map(topic => topic.trim()).filter(topic => {
      const key = topic.toLowerCase();
      if (!topic || seen.has(key)) return false;
      seen.add(key); return true;
    });
  }

  function rememberResearchRun(runId) {
    state.researchRunId = runId;
    localStorage.setItem(latestRunKey(), runId);
  }

  async function researchTopic() {
    const topics = briefTopics();
    if (!topics.length) { showError('Enter at least one research topic.'); return; }
    if (topics.length > 5) { showError('Use no more than five topics in one bounded research brief.'); return; }
    const name = $('#direct-research-name').value.trim();
    if (!name) { showError('Name this research brief before starting it.'); return; }
    const requestEpoch = state.workspaceEpoch;
    const requestWorkspace = state.workspace;
    const btn = $('#research-topic-btn');
    btn.disabled = true; btn.textContent = 'Saving brief...';
    const preview = $('#explore-preview');
    preview.replaceChildren(el('div', 'notice', `Saving a bounded research run for ${topics.length} topic(s). No findings are claimed yet.`));
    const candidateLimit = topics.length;
    const budget = {
      root_probe_candidates: candidateLimit,
      deep_read_candidates: candidateLimit,
      horizontal_llm_candidates: candidateLimit,
      threads_per_platform: 2,
      comments_per_thread: 20,
      max_thread_depth: 2,
      optional_enrichments: 0,
    };
    const payload = {
      workspace_id: requestWorkspace,
      name,
      lens_preset_id: $('#direct-preset').value === 'general-research' ? 'horizontal-explorer' : $('#direct-preset').value,
      candidates: topics.map(topic => ({ id: topic, keyword: topic, eligible: true })),
      required_depth: 'horizontal_analysis',
      budget,
    };
    try {
      const run = await api('/discovery/research-runs', {
        method: 'POST', body: JSON.stringify(payload),
      });
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return;
      const runId = run.id || run.run_id;
      rememberResearchRun(runId);
      state.researchRunStatus = 'planned';
      btn.textContent = 'Starting research...';
      await api(`/discovery/research-runs/${enc(runId)}/execute`, { method: 'POST' });
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return;
      state.researchRunStatus = 'running';
      preview.replaceChildren(el('div', 'notice', 'Research is collecting available conversations. You can leave this view and return from Findings. If the service restarts, reopening this run safely resumes it.'));
      await pollResearchRun(runId, { button: btn, preview });
    } catch (error) {
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return;
      showError(error.message);
      btn.disabled = false; btn.textContent = 'Start research';
      preview.replaceChildren(el('div', 'notice', `Research could not be started: ${error.message}`));
    }
  }

  const researchPolls = new Map();

  function pollResearchRun(runId, context = {}) {
    if (researchPolls.has(runId)) return researchPolls.get(runId);
    const poll = pollResearchRunOnce(runId, context)
      .finally(() => researchPolls.delete(runId));
    researchPolls.set(runId, poll);
    return poll;
  }

  async function pollResearchRunOnce(runId, { button = null, preview = null } = {}) {
    const requestEpoch = state.workspaceEpoch;
    const requestWorkspace = state.workspace;
    const isStale = () => (
      requestEpoch !== state.workspaceEpoch
      || requestWorkspace !== state.workspace
      || state.researchRunId !== runId
    );
    for (let attempt = 0; attempt < 300; attempt += 1) {
      if (isStale()) return null;
      const run = await api(`/discovery/research-runs/${enc(runId)}`);
      if (isStale()) return run;
      if (['planned', 'running'].includes(run.status) && attempt % 15 === 0) {
        try {
          await api(`/discovery/research-runs/${enc(runId)}/execute`, { method: 'POST' });
        } catch (error) {
          if (error.status !== 409) throw error;
        }
        if (isStale()) return run;
      }
      state.researchRunStatus = run.status;
      if (preview && run.status === 'running') {
        preview.textContent = `Research is running for ${(run.plan?.candidates || []).length} topic(s). Findings and source receipts remain durable after processing finishes.`;
      }
      if (terminalResearchStatuses.has(run.status)) {
        if (button) { button.disabled = false; button.textContent = 'Start research'; }
        if (run.status === 'complete' || run.status === 'partial') {
          await loadFindings(runId, { quiet: true });
          if (isStale()) return run;
          if (preview) preview.textContent = `Research ${run.status}. ${persistedFindings.length} saved result(s) are ready in Findings.`;
          toast(`Research ${run.status}: ${persistedFindings.length} finding(s)`);
        } else if (preview) {
          preview.textContent = `Research ${run.status}: ${run.error_category || 'No further detail was recorded.'}`;
        }
        await loadResearchHistory({ quiet: true });
        return run;
      }
      await wait(2000);
    }
    if (isStale()) return null;
    if (button) { button.disabled = false; button.textContent = 'Check Findings'; }
    if (preview) preview.textContent = 'Research is still active. Its latest status remains available in Findings.';
    return null;
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

  function selectedFamilyFilters() {
    return {
      perspectiveId: $('#global-perspective')?.value || '',
      stage: $('#global-stage')?.value || '',
      geo: ($('#global-geo')?.value || '').trim().toUpperCase(),
      includeRejected: $('#global-include-rejected')?.checked === true,
    };
  }

  async function loadGlobalExplore() {
    const grid = $('#family-grid');
    if (!grid) return;
    const epoch = state.workspaceEpoch;
    const requestEpoch = ++state.globalExploreEpoch;
    const selectedFamilyId = state.selectedFamily?.family_id || null;
    state.selectedFamily = null;
    loading(grid, 'Loading topic families');
    loading($('#family-detail'), 'Loading family evidence');
    $('#global-explore-status').textContent = 'Loading persisted topic families. No live sources are being called.';
    const filters = selectedFamilyFilters();
    const query = new URLSearchParams({ workspace_id: state.workspace });
    if (filters.perspectiveId) query.set('perspective_id', filters.perspectiveId);
    try {
      const data = await api(`/explore/families?${query}`);
      if (epoch !== state.workspaceEpoch || requestEpoch !== state.globalExploreEpoch) return;
      state.families = data.items || data.families || [];
      state.selectedFamily = state.families.find(item => item.family_id === selectedFamilyId) || null;
      const receipt = data.collection_performed === false
        ? 'Perspective applied to persisted evidence. No collection was performed.'
        : 'Persisted family evidence loaded.';
      $('#global-explore-status').textContent = `${receipt} ${state.families.length} families available before local filters.`;
      renderGlobalExplore();
      if (state.selectedFamily) renderFamilyDetail(state.selectedFamily);
      else $('#family-detail').replaceChildren(emptyState('Select a family', 'Evidence detail', 'Choose a topic family to inspect its persisted evidence and limitations.', true));
    } catch (error) {
      if (epoch !== state.workspaceEpoch || requestEpoch !== state.globalExploreEpoch) return;
      state.families = []; state.selectedFamily = null;
      grid.replaceChildren(emptyState('Unavailable', 'Global Explore could not be loaded', error.message, true));
      $('#family-detail').replaceChildren(emptyState('Unavailable', 'No family detail is available', 'Restore persisted family access and refresh.', true));
      $('#family-count').textContent = 'Unavailable';
      $('#global-explore-status').textContent = `Persisted evidence unavailable: ${error.message}`;
    }
  }

  function renderGlobalExplore() {
    const grid = $('#family-grid');
    const filters = selectedFamilyFilters();
    const visible = state.families.filter(family => {
      if (filters.stage && family.stage !== filters.stage) return false;
      if (filters.geo && String(family.geo || '').toUpperCase() !== filters.geo) return false;
      const unclear = family.stage === 'unclear' || String(family.status || '').toLowerCase() === 'rejected';
      return filters.includeRejected || !unclear;
    });
    $('#family-count').textContent = `${visible.length} of ${state.families.length}`;
    grid.replaceChildren();
    if (!visible.length) {
      grid.append(emptyState('No matching families', 'Nothing passed these view filters', 'Broaden the stage or region filter. Rejected and unclear families remain available when explicitly included.', true));
      return;
    }
    visible.forEach(family => {
      const card = el('article', `family-card${state.selectedFamily?.family_id === family.family_id ? ' selected' : ''}`);
      const header = el('div', 'family-card-head');
      const title = el('button', 'family-open', family.label || 'Unnamed topic family');
      title.type = 'button'; title.addEventListener('click', () => {
        state.selectedFamily = family; renderGlobalExplore(); renderFamilyDetail(family);
      });
      append(header, title, statusBadge(family.stage || 'unclear'));
      const explanation = family.what_it_is?.text || 'Not enough context yet.';
      const terms = (family.member_terms || []).slice(0, 4).map(item => item.term).filter(Boolean);
      const routes = (family.why_surfaced || []).filter(item => item.passed !== false).map(item => item.route).filter(Boolean);
      append(card, header, el('p', 'family-summary', explanation));
      if (terms.length) card.append(el('p', 'family-terms', terms.join(' · ')));
      const trajectory = family.trajectory || {};
      const meta = [
        trajectory.direction,
        trajectory.period?.start && trajectory.period?.end ? `period ${trajectory.period.start} to ${trajectory.period.end}` : null,
        routes.length ? `via ${routes.join(', ')}` : 'route not confirmed',
      ].filter(Boolean).join(' · ');
      card.append(el('p', 'family-meta mono', meta));
      grid.append(card);
    });
  }

  function renderFamilyDetail(family) {
    const detail = $('#family-detail'); detail.replaceChildren();
    const head = el('div', 'detail-head'); const intro = el('div');
    append(intro, el('p', 'eyebrow', family.category || family.geo || 'Topic family'), el('h2', '', family.label || 'Unnamed topic family'), statusBadge(family.stage || 'unclear'));
    const actions = el('div', 'actions');
    const investigate = el('button', 'primary', 'Investigate');
    investigate.addEventListener('click', () => {
      $('#direct-topic').value = family.label || '';
      $('.known-topic-section').open = true;
      $('#direct-topic').focus();
    });
    const monitor = el('button', 'quiet', 'Request monitor'); monitor.addEventListener('click', () => runFamilyAction(family, 'monitor'));
    const dismiss = el('button', 'quiet danger', 'Dismiss'); dismiss.addEventListener('click', () => runFamilyAction(family, 'dismiss'));
    append(actions, investigate, monitor, dismiss); append(head, intro, actions); detail.append(head);
    detail.append(el('p', 'lead-copy', family.what_it_is?.text || 'Not enough context yet.'));
    addDataSection(detail, 'Member terms', family.member_terms, 'No member terms were recorded.');
    addDataSection(detail, 'Why it surfaced', family.why_surfaced, 'No promotion route passed.');
    addDataSection(detail, 'Trajectory', family.trajectory, 'No comparable trajectory was recorded.');
    addDataSection(detail, 'Resonance', family.resonance, 'No supported engagement baseline was recorded.');
    addDataSection(detail, 'Independent corroboration', family.corroboration, 'Independent-root evidence is unavailable.');
    addDataSection(detail, 'Propagation', family.propagation, 'No repost or copy propagation was recorded.');
    addDataSection(detail, 'Conversation depth', family.conversation_depth, 'Conversation depth was not checked.');
    addDataSection(detail, 'Coverage', family.coverage, 'Source coverage was not recorded.');
    addDataSection(detail, 'Limitations', family.limitations, 'No limitations were recorded.');
    addDataSection(detail, 'Citations', family.what_it_is?.support, 'No cited context supports the explanation yet.');
  }

  async function runFamilyAction(family, actionType) {
    try {
      await api(`/explore/families/${enc(family.family_id)}/actions`, {
        method: 'POST',
        body: JSON.stringify({ workspace_id: state.workspace, action_type: actionType }),
      });
      toast(actionType === 'monitor' ? 'Monitoring request recorded' : 'Family dismissed for this workspace');
      await loadGlobalExplore();
    } catch (error) { showError(error.message); }
  }

  async function runExplore() {
    const results = $('#explore-results'); const button = $('#explore-form button[type=submit]');
    button.disabled = true; button.textContent = 'Searching…';
    results.replaceChildren(emptyState('Live search', 'Checking sources', 'Keep this page open while the bounded search completes.', true));
    $('#explore-preview').replaceChildren(el('strong', '', 'Running. '), document.createTextNode('Live sources are being checked within the reviewed limits.'));
    // Explicit scan modes: the Trend feed defaults to the cheap Trends
    // snapshot (zero social-source and zero LLM calls). "Confirmed checks
    // only" runs root_sweep — root social evidence, no threads, no LLM.
    // Deep reads and conversation analysis happen via research-runs only.
    const query = new URLSearchParams({ geo: $('#explore-geo').value.trim().toUpperCase(), mode: $('#explore-verified').checked ? 'root_sweep' : 'trends_snapshot', min_volume: $('#explore-volume').value, min_growth: $('#explore-growth').value, max_age_hours: $('#explore-age').value, gate_only: $('#explore-verified').checked ? 'true' : 'false' });
    try {
      const data = await api(`/discover?${query}`);
      state.candidates = data.keywords || [];
      state.discoveryRunId = data.run_id || null;
      state.discoveryRunStatus = data.run?.status || null;
      state.selectedCandidate = null; state.selectedForPlan.clear();
      $('#usage-run').value = state.discoveryRunId || '';
      $('#explore-preview').textContent = state.discoveryRunStatus === 'complete'
        ? `Completed discovery run ${state.discoveryRunId}. Results are held in this browser session.`
        : `Search returned ${state.candidates.length} results, but persisted completion status was not available.`;
      renderExploreResults(); renderFindings();
    } catch (error) {
      results.replaceChildren(emptyState('Failed', 'Search did not complete', error.message, true));
      $('#explore-preview').textContent = `Search failed: ${error.message}`;
    } finally { button.disabled = false; button.textContent = 'Review search'; }
  }

  function candidateName(candidate) { return candidate.keyword || candidate.name || candidate.query || candidate.id || 'Unnamed result'; }
  function candidateId(candidate, index) { return String(candidate._plannedId || candidate.candidate_id || candidate.id || candidate.keyword || candidate.name || index).trim().toLowerCase().split(/\s+/).join(' '); }

  function renderExploreResults() {
    const list = $('#explore-results'); list.replaceChildren();
    // Apply category filter
    const catFilter = $('#explore-cat-filter')?.value || '';
    const filtered = catFilter ? state.candidates.filter(c => (c.categories || c.category || '').includes(catFilter)) : state.candidates;
    $('#explore-count').textContent = catFilter ? `${filtered.length} of ${state.candidates.length}` : `${state.candidates.length} returned`;
    if (!filtered.length) { list.append(emptyState('Empty result', catFilter ? `No topics in "${catFilter}"` : 'No topics matched', catFilter ? 'Try a different category filter or clear it to see all results.' : 'The live search completed but returned no results within the selected filters.', true)); return; }
    filtered.forEach((candidate, index) => {
      const id = candidateId(candidate, index); const row = el('button', `data-row${state.selectedCandidate === candidate ? ' selected' : ''}`); row.type = 'button';
      const analysis = candidate.conversation_analysis || candidate.analysis || {};
      const growth = candidate.growth_pct ?? candidate.growth;
      const statusText = String(analysis.status || candidate.gate_status || '').toLowerCase();
      const statusDisplay = statusText === 'partial' ? 'Some sources checked' : statusText === 'complete' ? 'Verified' : statusText === 'not_checked' ? 'Not yet checked' : statusText ? statusText.replaceAll('_', ' ') : 'Not checked';
      const vol = candidate.search_volume ?? candidate.volume;
      append(row, el('span', 'row-title', candidateName(candidate)), el('span', 'row-copy', candidate.categories || candidate.category || candidate.conv_summary || candidate.description || 'No summary returned'), el('span', 'row-meta', `${vol != null ? count(vol) + ' searches' : 'Unknown volume'} · ${growth == null ? 'Unknown growth' : `${growth}% growth`} · ${statusDisplay}`));
      row.addEventListener('click', () => { state.selectedCandidate = candidate; renderExploreResults(); renderCandidateDetail(candidate, index); });
      const check = el('input'); check.type = 'checkbox'; check.checked = state.selectedForPlan.has(id); check.setAttribute('aria-label', `Select ${candidateName(candidate)} for research plan`);
      check.addEventListener('click', event => {
        event.stopPropagation();
        if (check.checked && state.selectedForPlan.size >= 5) {
          check.checked = false;
          showError('Use no more than five topics in one research brief.');
          return;
        }
        check.checked ? state.selectedForPlan.add(id) : state.selectedForPlan.delete(id);
        renderSelectionBar();
      });
      row.prepend(check); list.append(row);
    });
    const bar = el('div', 'selection-bar'); bar.id = 'selection-bar'; list.append(bar); renderSelectionBar();
  }

  function renderSelectionBar() {
    const bar = $('#selection-bar'); if (!bar) return;
    bar.replaceChildren(el('span', 'mono', `${state.selectedForPlan.size} selected`));
    const plan = el('button', 'primary', 'Research these topics');
    plan.id = 'create-plan-btn';
    plan.disabled = !state.selectedForPlan.size || ['planned', 'running'].includes(state.researchRunStatus);
    plan.addEventListener('click', createResearchPlan);
    bar.append(plan);
  }

  const BLOCKED_SOURCE_SUFFIXES = ['.example', '.invalid', '.localhost', '.local', '.internal', '.home', '.lan', '.test', '.onion'];

  function safeUrl(value) {
    const raw = String(value || '').trim();
    if (!raw || /[\u0000-\u0020\u007f]/.test(raw)) return null;
    try {
      const url = new URL(raw);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      if (url.username || url.password) return null;
      const host = url.hostname.replace(/\.$/, '').toLowerCase();
      if (!host || host.includes(':')) return null;
      const octets = host.split('.');
      if (octets.length === 4 && octets.every(part => /^\d{1,3}$/.test(part) && Number(part) <= 255)) {
        const [a, b] = octets.map(Number);
        const privateOrReserved = (
          a === 0 || a === 10 || a === 127 || a >= 224
          || (a === 100 && b >= 64 && b <= 127)
          || (a === 169 && b === 254)
          || (a === 172 && b >= 16 && b <= 31)
          || (a === 192 && (b === 0 || b === 168))
          || (a === 198 && (b === 18 || b === 19))
        );
        if (privateOrReserved) return null;
      } else {
        if (!host.includes('.')) return null;
        if (BLOCKED_SOURCE_SUFFIXES.some(suffix => host === suffix.slice(1) || host.endsWith(suffix))) return null;
        if (host.split('.').some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) return null;
      }
      return url.href;
    } catch (_) {
      return null;
    }
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
        const heading = item.claim || item.title || item.name || item.text || item.label || item.platform || item.id || 'Returned record';
        record.append(el('strong', '', heading));
        const copy = item.evidence || item.summary || item.description || item.excerpt || item.rationale || item.relationship;
        if (copy && copy !== heading) record.append(el('p', '', copy));
        const href = safeUrl(item.url || item.source_url || item.permalink);
        if (href) { const link = el('a', 'source-link', 'Open source'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; record.append(link); }
        const remainder = { ...item }; ['claim','title','text','label','platform','evidence','summary','description','excerpt','url','source_url','permalink'].forEach(key => delete remainder[key]);
        if (Object.keys(remainder).length) record.append(el('pre', 'raw-data', JSON.stringify(remainder, null, 2)));
      }
      section.append(record);
    }); parent.append(section);
  }

  function addCoverageSection(parent, coverage) {
    const section = el('section', 'data-section');
    section.append(el('h3', '', 'Coverage'));
    const definitions = [
      ['Raw records', coverage?.raw_records],
      ['Reviewed records', coverage?.deduplicated_records],
      ['Independent voices', coverage?.independent_voices],
      ['Threads', coverage?.thread_count],
      ['Platforms', coverage?.platform_count],
    ];
    const metrics = el('div', 'coverage-metrics');
    definitions.forEach(([label, value]) => {
      if (value === undefined || value === null) return;
      const item = el('div', 'coverage-metric');
      item.append(el('strong', '', String(value)), el('span', '', label));
      metrics.append(item);
    });
    section.append(metrics);
    const sources = Array.isArray(coverage?.source_status) ? coverage.source_status : [];
    if (sources.length) {
      section.append(el('h4', 'coverage-source-title', 'Source receipts'));
      sources.forEach(source => {
        const row = el('div', 'source-receipt');
        row.append(el('strong', '', String(source.platform || 'Source')));
        const counts = source.items_returned === undefined
          ? ''
          : `${source.items_returned} of ${source.items_requested ?? 'unknown'} records`;
        row.append(el('p', '', [source.status, counts, source.connector].filter(Boolean).join(' · ')));
        if (source.error) row.append(el('p', 'source-error', String(source.error)));
        section.append(row);
      });
    }
    parent.append(section);
  }

  function addEvidenceSection(parent, evidence) {
    const section = el('section', 'data-section evidence-section');
    section.append(el('h3', '', 'Evidence records'));
    if (!Array.isArray(evidence) || !evidence.length) {
      section.append(el('p', 'empty-copy', 'No citable source records were returned.'));
      parent.append(section);
      return;
    }
    evidence.forEach(item => {
      const record = el('article', 'data-record evidence-record');
      record.append(el('p', 'evidence-copy', item.text || 'Source record'));
      const metadata = [item.platform, item.object_type, fmtDate(item.published_at)]
        .filter(value => value && value !== 'Unknown').join(' · ');
      if (metadata) record.append(el('p', 'evidence-meta', metadata));
      if (item.id) record.append(el('code', 'evidence-id', item.id));
      if (safeUrl(item.url)) {
        const link = document.createElement('a');
        link.href = safeUrl(item.url); link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = 'Open source'; record.append(link);
      }
      section.append(record);
    });
    parent.append(section);
  }


  // ── Sparkline ──────────────────────────────────────────────
  function sparkline(values, width = 280, height = 48) {
    if (!values || values.length < 2) return null;
    const max = Math.max(...values), min = Math.min(...values);
    const range = max - min || 1;
    const step = width / (values.length - 1);
    const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - min) / range) * height).toFixed(1)}`);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '100%'); svg.setAttribute('height', height);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.classList.add('sparkline');
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('fill', 'none'); poly.setAttribute('stroke', '#7dd3a0');
    poly.setAttribute('stroke-width', '2'); poly.setAttribute('points', pts.join(' '));
    svg.appendChild(poly);
    if (values.length > 2) {
      const area = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
      area.setAttribute('fill', 'rgba(125,211,160,0.12)');
      area.setAttribute('points', `0,${height} ${pts.join(' ')} ${width},${height}`);
      svg.insertBefore(area, poly);
    }
    return svg;
  }

  function renderRelatedTags(label, items) {
    if (!items || !items.length) return null;
    const wrap = el('div', 'related-tag-group');
    wrap.append(el('p', 'tag-label', label));
    const tagRow = el('div', 'tag-row');
    items.slice(0, 8).forEach(item => {
      const text = item.query || item.keyword || item.term || (typeof item === 'string' ? item : Object.values(item)[0]);
      tagRow.append(el('span', 'tag', String(text)));
    });
    wrap.append(tagRow);
    return wrap;
  }

  // ── Detail panel (async enrichment) ────────────────────────
  let _detailEpoch = 0;

  async function renderCandidateDetail(candidate, index = 0) {
    const detail = $('#explore-detail'); detail.replaceChildren();
    const myEpoch = ++_detailEpoch;
    const analysis = candidate.conversation_analysis || candidate.analysis || {};

    // Header
    const head = el('div', 'detail-head'); const intro = el('div');
    append(intro, el('p', 'eyebrow', candidate.categories || candidate.category || 'Trending topic'), el('h2', '', candidateName(candidate)), statusBadge(analysis.status || candidate.gate_status || 'not_checked'));
    const actions = el('div', 'actions');
    const id = candidateId(candidate, index);

    // Quick research button — reads conversations immediately
    const quickBtn = el('button', 'primary', 'Research conversations');
    quickBtn.addEventListener('click', () => {
      const topic = candidateName(candidate);
      $('#direct-topic').value = topic;
      $('#research-topic-btn').click();
    });
    actions.append(quickBtn);

    if (state.researchRunId && state.selectedForPlan.has(id)) {
      const promote = el('button', 'quiet', 'Promote in plan');
      promote.addEventListener('click', () => promoteCandidate(id));
      actions.append(promote);
    }
    append(head, intro, actions); detail.append(head);

    // Related terms from the trend object (always available)
    if (candidate.related_terms && candidate.related_terms.length) {
      const tags = renderRelatedTags('Related searches', candidate.related_terms);
      if (tags) detail.append(tags);
    }

    // If already analyzed, show findings
    const summary = analysis.summary || analysis.finding || candidate.conv_summary || candidate.description;
    if (summary) addDataSection(detail, 'Summary', summary, '');
    if (analysis.signals && analysis.signals.length) addDataSection(detail, 'Signals', analysis.signals, 'No signals extracted.');
    if (analysis.evidence && analysis.evidence.length) addDataSection(detail, 'Evidence', analysis.evidence, 'No evidence records.');
    if (analysis.limitations && analysis.limitations.length) addDataSection(detail, 'Limitations', analysis.limitations, 'No limitations reported.');

    // Stats
    if (!analysis.signals && !analysis.evidence && !summary) {
      const stats = el('div', 'definition-list');
      const growth = candidate.growth_pct ?? candidate.growth;
      append(stats,
        el('dt', '', 'Search volume'), el('dd', '', count(candidate.search_volume ?? candidate.volume)),
        el('dt', '', 'Growth'), el('dd', '', growth == null ? 'Unknown' : `${growth}%`),
        el('dt', '', 'Category'), el('dd', '', value(candidate.categories || candidate.category)),
      );
      detail.append(stats);
    }

    // Live enrichment: sparkline + related queries from Google Trends
    const enrichDiv = el('div', 'trend-enrichment');
    enrichDiv.append(el('p', 'muted loading-text', 'Loading trend chart and related searches...'));
    detail.append(enrichDiv);

    try {
      const geo = ($('#explore-geo')?.value || 'US').trim().toUpperCase();
      const kw = candidateName(candidate);
      const enriched = await api(`/discover/trend-detail?keyword=${enc(kw)}&geo=${enc(geo)}`);
      if (myEpoch !== _detailEpoch) return; // user clicked another trend

      enrichDiv.replaceChildren();

      // Sparkline chart
      if (enriched.timeline && enriched.timeline.length > 1) {
        const chartBox = el('div', 'chart-box');
        chartBox.append(el('p', 'section-label', 'Search interest — last 7 days'));
        const values = enriched.timeline.map(t => t.value);
        const spark = sparkline(values);
        if (spark) chartBox.append(spark);
        const lo = Math.min(...values), hi = Math.max(...values);
        const recent = values.slice(-4).reduce((a, b) => a + b, 0) / Math.min(4, values.length);
        const early = values.slice(0, 4).reduce((a, b) => a + b, 0) / Math.min(4, values.length);
        const velocity = early > 0 ? Math.round(((recent - early) / early) * 100) : 0;
        const velText = velocity > 0 ? `↑ ${velocity}% accelerating` : velocity < 0 ? `↓ ${Math.abs(velocity)}% cooling` : 'Steady';
        chartBox.append(el('p', 'muted small', `Range ${lo}\u2013${hi} (0\u2013100 scale) · ${velText}`));
        enrichDiv.append(chartBox);
      }

      // Related queries from Google Trends
      const rising = enriched.related_rising || [];
      const top = enriched.related_top || [];
      if (rising.length || top.length) {
        const rqBox = el('div', 'related-queries-box');
        rqBox.append(el('p', 'section-label', 'What people also search for'));
        if (rising.length) { const t = renderRelatedTags('Rising fast', rising); if (t) rqBox.append(t); }
        if (top.length) { const t = renderRelatedTags('Most searched', top); if (t) rqBox.append(t); }
        enrichDiv.append(rqBox);
      }

      if (!enriched.timeline?.length && !rising.length && !top.length) {
        enrichDiv.append(el('p', 'muted small', enriched.error
          ? `Trend chart unavailable (${enriched.error}). Click "Research conversations" to read what people are saying.`
          : 'No additional trend data available. Click "Research conversations" to read social discussions.'));
      }
    } catch (error) {
      if (myEpoch !== _detailEpoch) return;
      enrichDiv.replaceChildren(el('p', 'muted small', `Could not load trend details: ${error.message}`));
    }
  }

  async function createResearchPlan(event) {
    if (!state.selectedForPlan.size) return;
    if (state.selectedForPlan.size > 5) {
      showError('Use no more than five topics in one research brief.');
      return;
    }
    const requestEpoch = state.workspaceEpoch;
    const requestWorkspace = state.workspace;
    if (state.researchRunId) {
      try {
        const current = activeResearchRun?.id === state.researchRunId
          ? activeResearchRun
          : await api(`/discovery/research-runs/${enc(state.researchRunId)}`);
        if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return;
        if (current && !terminalResearchStatuses.has(current.status)) {
          toast('Finish or cancel the active research run before creating another.');
          return;
        }
      } catch (error) {
        if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return;
        showError(error.message);
        return;
      }
    }
    if (event?.currentTarget) event.currentTarget.disabled = true;
    const chosen = state.candidates.filter((candidate, index) => state.selectedForPlan.has(candidateId(candidate, index)));
    const topicCount = chosen.length;
    const budget = { root_probe_candidates: topicCount, deep_read_candidates: topicCount, horizontal_llm_candidates: topicCount, threads_per_platform: 2, comments_per_thread: 20, max_thread_depth: 2, optional_enrichments: 0 };
    const selectedLens = state.lenses.find(lens => lens.id === $('#explore-lens').value);
    const lensDepth = selectedLens?.latest_version?.compiled_requirements?.required_depth || null;
    const payload = { workspace_id: requestWorkspace, name: `Trend research · ${chosen.map(candidateName).slice(0, 3).join(', ')}`, lens_preset_id: 'horizontal-explorer', source_discovery_run_id: state.discoveryRunId, candidates: chosen, required_depth: 'horizontal_analysis', lens_required_depth: lensDepth, lens: selectedLens ? { id: selectedLens.id, version: selectedLens.latest_version?.version || selectedLens.latest_version_number, required_depth: lensDepth } : null, budget };
    try {
      const run = await api('/discovery/research-runs', { method: 'POST', body: JSON.stringify(payload) });
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return;
      (run.plan?.candidates || []).forEach(planned => {
        const original = chosen.find(candidate => candidateId(candidate, 0) === planned.candidate_id);
        if (original) original._plannedId = planned.candidate_id;
      });
      rememberResearchRun(run.id || run.run_id);
      state.researchRunStatus = run.status || 'planned';
      toast(`Saved ${chosen.length} topic(s) for research`);
      renderSelectionBar();
      renderFindings();
      if (state.selectedCandidate) renderCandidateDetail(state.selectedCandidate, state.candidates.indexOf(state.selectedCandidate));
      const preview = $('#explore-preview');
      preview.replaceChildren();
      const statusText = el('div', 'notice', `Ready to research ${chosen.length} topic(s). Click "Start research" to read conversations and extract what people are saying.`);
      const execBtn = el('button', 'primary', 'Start research');
      execBtn.id = 'execute-run-btn';
      execBtn.addEventListener('click', executeResearchRun);
      const findBtn = el('button', 'quiet', 'View results');
      findBtn.id = 'load-findings-btn';
      findBtn.addEventListener('click', () => loadFindings());
      append(preview, statusText, execBtn, findBtn);
    } catch (error) {
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return;
      showError(error.message);
      if (event?.currentTarget) event.currentTarget.disabled = false;
    }
  }

  async function promoteCandidate(candidateIdValue) {
    if (!state.researchRunId) return;
    try {
      const result = await api(`/discovery/research-runs/${enc(state.researchRunId)}/candidates/${enc(candidateIdValue)}/promote`, { method: 'POST' });
      toast(result.manual_promoted || result.candidate?.manual_promoted ? 'Candidate promoted in the plan' : 'Promotion request saved');
    } catch (error) { showError(error.message); }
  }

  async function executeResearchRun() {
    if (!state.researchRunId) return;
    const btn = $('#execute-run-btn');
    if (!btn) return;
    btn.disabled = true; btn.textContent = 'Starting research...';
    const preview = $('#explore-preview');
    const progress = el('div', 'notice', 'Starting the persisted research run. You may leave this view after it begins.');
    preview.append(progress);
    try {
      await api(`/discovery/research-runs/${enc(state.researchRunId)}/execute`, { method: 'POST' });
      progress.textContent = 'Research is running in the background. Its status and findings remain available after reload.';
      await pollResearchRun(state.researchRunId, { button: btn, preview: progress });
      if (['complete', 'partial'].includes(state.researchRunStatus)) {
        const viewFindings = el('button', 'primary', 'View results');
        viewFindings.addEventListener('click', () => { showView('findings'); loadResearchHistory(); });
        preview.append(viewFindings);
      }
    } catch (error) {
      showError(error.message);
      btn.disabled = false; btn.textContent = 'Start research';
      progress.textContent = `Failed to start: ${error.message}`;
    }
  }

  async function startSavedResearchRun(runId, button) {
    const requestEpoch = state.workspaceEpoch;
    const requestWorkspace = state.workspace;
    button.disabled = true;
    button.textContent = 'Starting research...';
    try {
      await api(`/discovery/research-runs/${enc(runId)}/execute`, { method: 'POST' });
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace || state.researchRunId !== runId) return;
      state.researchRunStatus = 'running';
      if (activeResearchRun?.id === runId) activeResearchRun.status = 'running';
      renderFindings();
      await pollResearchRun(runId, { button });
    } catch (error) {
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace || state.researchRunId !== runId) return;
      button.disabled = false;
      button.textContent = 'Start saved research';
      showError(error.message);
    }
  }

  let persistedFindings = [];
  let activeResearchRun = null;

  function researchRunName(run) {
    const named = String(run.plan?.name || '').trim();
    if (named) return named;
    const topics = (run.plan?.candidates || []).map(item => item.candidate?.keyword).filter(Boolean);
    return topics.slice(0, 3).join(', ') || `Research run ${run.id}`;
  }

  async function loadResearchHistory({ quiet = false } = {}) {
    const target = $('#research-run-history');
    if (!target) return [];
    const requestEpoch = state.workspaceEpoch;
    const requestWorkspace = state.workspace;
    if (!quiet) loading(target, 'Loading saved research');
    try {
      const data = await api(`/discovery/research-runs?workspace_id=${enc(requestWorkspace)}`);
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return [];
      const runs = data.runs || [];
      target.replaceChildren();
      if (!runs.length) {
        target.append(emptyState('Saved research', 'No research runs yet', 'Start a bounded research brief in Explore. The run and its evidence will remain available here.', true));
        persistedFindings = []; activeResearchRun = null; renderFindings();
        return runs;
      }
      const remembered = localStorage.getItem(latestRunKey());
      const selected = runs.find(run => run.id === state.researchRunId)
        || runs.find(run => run.id === remembered) || runs[0];
      const list = el('div', 'research-run-list');
      runs.slice(0, 12).forEach(run => {
        const row = el('button', `research-run-row${run.id === selected.id ? ' selected' : ''}`);
        row.type = 'button';
        append(row,
          el('span', 'row-title', researchRunName(run)),
          statusBadge(run.status),
          el('span', 'row-copy', `${(run.plan?.candidates || []).length} topic(s) · ${run.plan?.lens_preset?.name || 'General research'}`),
          el('span', 'row-meta mono', run.created_at || 'Creation time unavailable'),
        );
        row.addEventListener('click', async () => {
          rememberResearchRun(run.id); activeResearchRun = run; state.researchRunStatus = run.status;
          await loadFindings(run.id, { quiet: true });
          await loadResearchHistory({ quiet: true });
          if (run.status === 'running') pollResearchRun(run.id).catch(error => showError(error.message));
        });
        list.append(row);
      });
      target.append(list);
      rememberResearchRun(selected.id); activeResearchRun = selected; state.researchRunStatus = selected.status;
      await loadFindings(selected.id, { quiet: true });
      if (selected.status === 'running') pollResearchRun(selected.id).catch(error => showError(error.message));
      return runs;
    } catch (error) {
      if (requestEpoch !== state.workspaceEpoch || requestWorkspace !== state.workspace) return [];
      target.replaceChildren(emptyState('Unavailable', 'Saved research could not be loaded', error.message, true));
      if (!quiet) showError(error.message);
      return [];
    }
  }

  async function loadFindings(runId = state.researchRunId, { quiet = false } = {}) {
    if (!runId) { persistedFindings = []; renderFindings(); return []; }
    const requestEpoch = state.workspaceEpoch;
    rememberResearchRun(runId);
    try {
      const [findingData, run] = await Promise.all([
        api(`/discovery/research-runs/${enc(runId)}/findings`),
        api(`/discovery/research-runs/${enc(runId)}`),
      ]);
      if (requestEpoch !== state.workspaceEpoch || state.researchRunId !== runId) return [];
      persistedFindings = findingData.findings || [];
      activeResearchRun = run; state.researchRunStatus = run.status;
      if (!quiet && persistedFindings.length) toast(`${persistedFindings.length} saved result(s) loaded.`);
      renderFindings();
      return persistedFindings;
    } catch (error) {
      if (requestEpoch !== state.workspaceEpoch || state.researchRunId !== runId) return [];
      persistedFindings = [];
      const content = $('#findings-content');
      if (content) content.replaceChildren(emptyState('Unavailable', 'Findings could not be rendered', error.message, true));
      if (!quiet) showError(error.message);
      return [];
    }
  }

  function evidenceById(analysis) {
    return new Map((analysis.evidence || []).map(item => [item.id, item]));
  }

  function hasRenderableCitations(evidenceIds, lookup) {
    return [...new Set(Array.isArray(evidenceIds) ? evidenceIds : [])]
      .some(id => safeUrl(lookup.get(id)?.url));
  }

  function appendCitationLinks(parent, evidenceIds, lookup) {
    const links = [...new Set(Array.isArray(evidenceIds) ? evidenceIds : [])]
      .map(id => ({ id, record: lookup.get(id) }))
      .filter(item => safeUrl(item.record?.url));
    if (!links.length) return;
    const row = el('div', 'citation-links');
    row.append(el('span', 'mono', 'Sources'));
    links.forEach((item, index) => {
      const link = el('a', 'source-link', `[${index + 1}] ${value(item.record.platform)}`);
      link.href = safeUrl(item.record.url);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.title = item.id;
      row.append(link);
    });
    parent.append(row);
  }

  function addEntitySection(parent, entities, lookup) {
    const section = el('section', 'evidence-section');
    section.append(el('h3', '', 'Entities and alternatives'));
    const citableEntities = Array.isArray(entities)
      ? entities.filter(entity => hasRenderableCitations(entity.evidence_ids, lookup))
      : [];
    if (!citableEntities.length) {
      section.append(el('p', 'muted', 'No citation-backed companies, products, or alternatives were extracted.'));
      parent.append(section);
      return;
    }
    citableEntities.forEach(entity => {
      const record = el('article', 'evidence-record');
      record.append(
        el('strong', '', value(entity.name)),
        el('p', '', `${value(entity.type)} · ${value(entity.relationship)}`),
      );
      appendCitationLinks(record, entity.evidence_ids, lookup);
      section.append(record);
    });
    parent.append(section);
  }

  function renderFindings() {
    const content = $('#findings-content'); content.replaceChildren();
    if (persistedFindings.length) {
      const preset = activeResearchRun?.plan?.lens_preset || null;
      const preferredKinds = new Set(preset?.suggested_signal_kinds || []);
      append(content,
        el('p', 'eyebrow', researchRunName(activeResearchRun || { id: state.researchRunId, plan: {} })),
        el('div', 'notice', preset
          ? `${preset.name} reorders relevant signal types for review. The canonical evidence and non-matching signals remain preserved.`
          : 'Canonical horizontal evidence. No use-case interpretation was applied.'),
      );
      persistedFindings.forEach(finding => {
        const analysis = finding.analysis || {};
        const citationLookup = evidenceById(analysis);
        const block = el('article', 'subject-block');
        const title = el('h3', '', finding.topic || finding.candidate_id);
        const badge = statusBadge(analysis.status || finding.status);
        const dl = el('dl', 'definition-list');
        append(dl,
          el('dt', '', 'Behavior type'), el('dd', '', value(analysis.behavior_type)),
          el('dt', '', 'Direction'), el('dd', '', value(analysis.direction)),
          el('dt', '', 'Independent voices'), el('dd', '', count(analysis.independent_voice_count)),
        );
        append(block, title, badge);
        if (analysis.summary && hasRenderableCitations(analysis.summary_evidence_ids, citationLookup)) {
          const summary = el('div', 'finding-summary');
          summary.append(el('p', '', analysis.summary));
          appendCitationLinks(summary, analysis.summary_evidence_ids, citationLookup);
          block.append(summary);
        }
        block.append(dl);
        const signals = [...(analysis.signals || [])]
          .filter(signal => hasRenderableCitations(signal.evidence_ids, citationLookup))
          .sort((left, right) => Number(preferredKinds.has(right.kind)) - Number(preferredKinds.has(left.kind)));
        if (signals.length) {
          const sig = el('section', 'evidence-section'); sig.append(el('h3', '', 'Signals'));
          signals.forEach(signal => {
            const rec = el('article', `evidence-record${preferredKinds.has(signal.kind) ? ' focused-signal' : ''}`);
            rec.append(el('strong', '', `${value(signal.kind).replaceAll('_', ' ')} · ${value(signal.polarity)}`), el('p', '', signal.claim));
            const metrics = [];
            if (signal.independent_voices != null) metrics.push(`${signal.independent_voices} independent voice(s)`);
            if (signal.thread_count != null) metrics.push(`${signal.thread_count} thread(s)`);
            if (signal.confidence != null) metrics.push(`confidence ${signal.confidence}`);
            if (metrics.length) rec.append(el('span', 'mono', metrics.join(' · ')));
            appendCitationLinks(rec, signal.evidence_ids, citationLookup);
            sig.append(rec);
          });
          block.append(sig);
        }
        addEntitySection(block, analysis.entities, citationLookup);
        addCoverageSection(block, analysis.coverage || {});
        addEvidenceSection(block, analysis.evidence || []);
        addDataSection(block, 'Limitations', analysis.limitations, 'No limitations reported.');
        content.append(block);
      });
      return;
    }
    if (activeResearchRun?.status === 'planned') {
      const stateCard = emptyState('Research planned', researchRunName(activeResearchRun), 'This saved brief has not collected or interpreted any sources yet.', true);
      const start = el('button', 'primary', 'Start saved research');
      start.type = 'button';
      start.addEventListener('click', () => startSavedResearchRun(activeResearchRun.id, start));
      content.append(stateCard, start);
      return;
    }
    if (activeResearchRun?.status === 'running') {
      content.append(emptyState('Research running', researchRunName(activeResearchRun), 'Collection and analysis are still active. This view will recover the durable run after reload.', true));
      return;
    }
    if (activeResearchRun?.status === 'error') {
      content.append(emptyState('Research failed', researchRunName(activeResearchRun), activeResearchRun.error_category || 'No error detail was recorded.', true));
      return;
    }
    if (activeResearchRun) {
      content.append(emptyState('No findings', researchRunName(activeResearchRun), 'The run completed without a persisted finding. Check its source coverage and limitations before changing the brief.', true));
      return;
    }
    content.append(emptyState('Saved research', 'No run selected', 'Start a research brief in Explore or select a saved run above.', true));
  }

  async function loadLenses() {
    const target = $('#lens-list'); const epoch = state.workspaceEpoch; loading(target, 'Loading lenses');
    try { await ensureLenses(true); if (epoch === state.workspaceEpoch) renderLenses(); }
    catch (error) { if (epoch === state.workspaceEpoch) target.replaceChildren(emptyState('Unavailable', 'Lenses could not be loaded', error.message, true)); }
  }

  async function ensureLenses(refresh = false) {
    if (refresh || !state.lenses.length) {
      const epoch = state.workspaceEpoch;
      const lenses = await api(`${workspacePath()}/lenses`);
      if (epoch !== state.workspaceEpoch) return state.lenses;
      state.lenses = lenses;
    }
    const options = state.lenses.map(lens => ({ value: lens.id, label: `${lens.name} · v${value(lens.latest_version?.version || lens.latest_version_number)}` }));
    [$('#explore-lens'), $('#subject-form select[name=lens_id]'), $('#global-perspective')].filter(Boolean).forEach(select => {
      const current = select.value; select.replaceChildren();
      const none = el('option', '', select.id === 'global-perspective' ? 'All evidence' : 'No lens'); none.value = ''; select.append(none);
      options.forEach(item => { const option = el('option', '', item.label); option.value = item.value; select.append(option); });
      if ([...select.options].some(option => option.value === current)) select.value = current;
    });
    return state.lenses;
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

  function resetLensForm() { const form = $('#lens-form'); form.reset(); form.elements.lens_id.value = ''; form.elements.spec.value = '{"objective":"Find unmet needs","criteria":[{"criterion_id":"unmet_need","label":"Unmet need","feature_key":"unmet_need","mode":"display","weight":0,"missing_policy":"keep_unknown"}]}'; $('#lens-dialog-title').textContent = 'Create lens'; }
  async function duplicateLens(lens) { const name = prompt('Name for the duplicate', `${lens.name} copy`); if (!name) return; try { await api(`${workspacePath()}/lenses/${enc(lens.id)}/duplicate`, { method: 'POST', body: JSON.stringify({ name }) }); toast('Lens duplicated'); await loadLenses(); } catch (error) { showError(error.message); } }
  async function archiveLens(lens) { if (!confirm(`Archive “${lens.name}”?`)) return; try { await api(`${workspacePath()}/lenses/${enc(lens.id)}/archive`, { method: 'POST' }); toast('Lens archived'); await loadLenses(); } catch (error) { showError(error.message); } }

  async function renderMonitors() {
    const target = $('#monitor-list'); const epoch = state.workspaceEpoch; loading(target, 'Loading monitors');
    try {
      if (!state.projects.length) { const data = await api(`${workspacePath()}/projects`); if (epoch !== state.workspaceEpoch) return; state.projects = data.projects || []; }
      const entries = await Promise.all(state.projects.map(async project => ({ project, subjects: (await api(`${projectPath(project.id)}/subjects`)).subjects || [] })));
      if (epoch !== state.workspaceEpoch) return;
      target.replaceChildren(); const all = entries.flatMap(entry => entry.subjects.map(subject => ({ project: entry.project, subject })));
      if (!all.length) { target.append(emptyState('Empty workspace', 'No subjects to monitor', 'Add a subject inside a project first.', true)); return; }
      const table = el('table', 'data-table'); const hrow = el('tr'); ['Subject', 'Project', 'Cadence', 'State', 'Action'].forEach(name => hrow.append(el('th', '', name))); const thead = el('thead'); thead.append(hrow); const body = el('tbody');
      all.forEach(({ project, subject }) => {
        const row = el('tr'); append(row, el('td', '', subject.name), el('td', '', project.name), el('td', 'mono', subject.cadence_minutes == null ? 'Not available' : `${subject.cadence_minutes} min`)); const stat = el('td'); stat.append(statusBadge(subject.active ? 'active' : 'paused')); row.append(stat);
        const cell = el('td'); const button = el('button', 'quiet', subject.active ? 'Pause' : 'Start'); button.addEventListener('click', () => setMonitor(project, subject, !subject.active)); cell.append(button); row.append(cell); body.append(row);
      }); table.append(thead, body); target.append(table);
    } catch (error) { if (epoch === state.workspaceEpoch) target.replaceChildren(emptyState('Unavailable', 'Monitors could not be loaded', error.message, true)); }
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

  // ── Guided tour ────────────────────────────────────────────
  // Data-driven steps. Update this array as the product evolves.
  const tourSteps = [
    { view: 'projects', target: '#projects-title', placement: 'bottom', title: 'Welcome to Bounty', body: 'Bounty finds what people are saying across the web, reads the conversations, and gives you cited signals you can act on. Let\'s walk through how it works.' },
    { view: 'projects', target: '[data-open="project-dialog"]', placement: 'bottom', title: 'Start with a project', body: 'A project organizes your research objective. Create one for each thing you want to understand. Each project can hold multiple monitored subjects.' },
    { view: 'explore', target: '#direct-topic', placement: 'bottom', title: 'Research any topic', body: 'Type a company, product, or question here. Bounty goes straight to reading conversations — no need to browse trends. This is the fastest way to research something specific.' },
    { view: 'explore', target: '.trends-section', placement: 'bottom', title: 'Or browse trends', body: 'Expand this to see what\'s trending on Google. Filter by region, volume, and growth. Useful for discovering emerging topics you didn\'t know about.' },
    { view: 'explore', target: '#explore-results', placement: 'right', title: 'Results', body: 'Topics appear here. "Searches" = how many people searched for it. "Growth" = how fast it\'s rising. Select topics and click "Research these topics" to read what people are actually saying.' },
    { view: 'findings', target: '#findings-title', placement: 'bottom', title: 'Read the findings', body: 'Completed runs show their findings here: extracted signals with quotes, evidence records, coverage states, and honest limitations. If evidence is thin, Bounty says so.' },
    { view: 'lenses', target: '#lenses-title', placement: 'bottom', title: 'Define evaluation lenses', body: 'Lenses are versioned criteria for reading findings in context. Investing, product research, and marketing can each have different lenses. Nothing about the core model is domain-specific.' },
    { view: 'monitors', target: '#monitors-title', placement: 'bottom', title: 'Monitor subjects', body: 'Turn any subject into a recurring monitor. Bounty re-reads conversations on your schedule and flags what changed.' },
    { view: 'usage', target: '#usage-title', placement: 'bottom', title: 'Track every call', body: 'Usage shows exact receipts: source calls, LLM calls, cache hits, tokens consumed. No estimates, no hidden costs. This is your audit trail.' },
  ];

  function startTour() {
    let stepIndex = 0;
    const overlay = el('div', 'tour-overlay'); overlay.id = 'tour-overlay';
    const spotlight = el('div', 'tour-spotlight'); spotlight.id = 'tour-spotlight';
    const card = el('div', 'tour-card'); card.id = 'tour-card';
    overlay.append(spotlight, card);
    document.body.append(overlay);

    function showStep(i) {
      stepIndex = Math.max(0, Math.min(i, tourSteps.length - 1));
      const step = tourSteps[stepIndex];
      if (step.view) showView(step.view);

      // Wait for view switch to render before measuring
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const target = $(step.target);
          if (!target) { // Skip if element missing
            if (stepIndex < tourSteps.length - 1) return showStep(stepIndex + 1);
            return endTour();
          }
          target.scrollIntoView({ behavior: 'smooth', block: 'center' });

          requestAnimationFrame(() => {
            const rect = target.getBoundingClientRect();
            const pad = 8;
            spotlight.style.top = `${rect.top - pad}px`;
            spotlight.style.left = `${rect.left - pad}px`;
            spotlight.style.width = `${rect.width + pad * 2}px`;
            spotlight.style.height = `${rect.height + pad * 2}px`;

            // Position card
            const cardW = 380;
            const cardH = 220;
            let cardTop, cardLeft;
            if (step.placement === 'bottom') { cardTop = rect.bottom + 16; cardLeft = rect.left; card.className = 'tour-card tour-bottom'; }
            else if (step.placement === 'top') { cardTop = rect.top - cardH - 16; cardLeft = rect.left; card.className = 'tour-card tour-top'; }
            else { cardTop = rect.top; cardLeft = rect.right + 16; card.className = 'tour-card'; }
            // Keep on screen
            cardLeft = Math.max(16, Math.min(cardLeft, window.innerWidth - cardW - 16));
            cardTop = Math.max(16, Math.min(cardTop, window.innerHeight - cardH - 16));
            card.style.top = `${cardTop}px`;
            card.style.left = `${cardLeft}px`;

            // Build card content
            const inner = el('div', 'tour-card-inner');
            inner.replaceChildren();
            inner.append(el('div', 'tour-step-num', `Step ${stepIndex + 1} of ${tourSteps.length}`));
            inner.append(el('h3', '', step.title));
            inner.append(el('p', '', step.body));
            const nav = el('div', 'tour-nav');
            const navLeft = el('div', 'tour-nav-left');
            const skip = el('button', 'skip', 'Skip');
            skip.addEventListener('click', endTour);
            navLeft.append(skip);
            const navRight = el('div', 'tour-nav-right');
            // Dots
            const dots = el('div', 'tour-dots');
            tourSteps.forEach((_, di) => {
              const dot = el('button', `tour-dot${di === stepIndex ? ' active' : ''}`);
              dot.addEventListener('click', () => showStep(di));
              dots.append(dot);
            });
            navRight.append(dots);
            if (stepIndex > 0) {
              const prev = el('button', '', 'Back');
              prev.addEventListener('click', () => showStep(stepIndex - 1));
              navRight.append(prev);
            }
            if (stepIndex < tourSteps.length - 1) {
              const next = el('button', 'primary', 'Next');
              next.addEventListener('click', () => showStep(stepIndex + 1));
              navRight.append(next);
            } else {
              const done = el('button', 'primary', 'Get started');
              done.addEventListener('click', endTour);
              navRight.append(done);
            }
            nav.append(navLeft, navRight);
            inner.append(nav);
            card.replaceChildren(inner);
          });
        });
      });
    }

    function endTour() {
      const ov = $('#tour-overlay');
      if (ov) ov.remove();
      localStorage.setItem('bounty.tourCompleted', '1');
    }

    showStep(0);
  }

  function bind() {
    $('#workspace-key').value = state.workspace;
    $$('.nav-item').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
    $$('[data-view-link]').forEach(button => button.addEventListener('click', () => showView(button.dataset.viewLink)));
    $$('[data-open]').forEach(button => button.addEventListener('click', async () => { if (button.dataset.open === 'lens-dialog') resetLensForm(); if (button.dataset.open === 'subject-dialog') { try { await ensureLenses(); } catch (error) { showError(error.message); return; } } $(`#${button.dataset.open}`).showModal(); }));
    $$('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
    $('#menu-toggle').addEventListener('click', event => { const open = $('#sidebar').classList.toggle('open'); event.currentTarget.setAttribute('aria-expanded', String(open)); });
    $('#save-workspace').addEventListener('click', () => {
      const key = $('#workspace-key').value.trim() || 'default';
      state.workspaceEpoch += 1;
      state.workspace = key;
      localStorage.setItem('bounty.workspace', key);
      state.project = null;
      state.projects = [];
      state.lenses = [];
      state.families = [];
      state.selectedFamily = null;
      state.candidates = [];
      state.selectedCandidate = null;
      state.selectedForPlan.clear();
      state.discoveryRunId = null;
      state.discoveryRunStatus = null;
      state.researchRunId = null;
      state.researchRunStatus = null;
      persistedFindings = [];
      activeResearchRun = null;
      state.subjects.clear();
      toast(`Using workspace ${key}`);
      showView('projects');
    });
    $('#set-token').addEventListener('click', () => { const token = prompt('API bearer token. Leave blank to clear this tab’s token.', getToken()); if (token === null) return; token.trim() ? sessionStorage.setItem('bounty.apiToken', token.trim()) : sessionStorage.removeItem('bounty.apiToken'); toast(token.trim() ? 'API token saved for this tab' : 'API token cleared'); });
    $('#project-form').addEventListener('submit', createProject); $('#subject-form').addEventListener('submit', createSubject); $('#lens-form').addEventListener('submit', saveLens); $('#explore-form').addEventListener('submit', reviewExplore); $('#load-usage').addEventListener('click', loadUsage);
 $('#start-tour').addEventListener('click', startTour);
 $('#research-topic-btn').addEventListener('click', researchTopic);
 $('#direct-topic').addEventListener('keydown', event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); researchTopic(); } });
 $('#explore-cat-filter')?.addEventListener('change', renderExploreResults);
 $('#refresh-families')?.addEventListener('click', loadGlobalExplore);
 $('#global-perspective')?.addEventListener('change', loadGlobalExplore);
 $('#global-stage')?.addEventListener('change', renderGlobalExplore);
 $('#global-geo')?.addEventListener('input', renderGlobalExplore);
 $('#global-include-rejected')?.addEventListener('change', renderGlobalExplore);
 // Auto-start on first visit
 if (!localStorage.getItem('bounty.tourCompleted') && !location.hash) setTimeout(startTour, 600);
 }

  bind();
  const initial = location.hash.slice(1); showView(['projects','explore','findings','lenses','monitors','usage'].includes(initial) ? initial : 'projects');
})();
