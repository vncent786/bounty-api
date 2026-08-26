(() => {
  'use strict';

  const TOKEN_KEY = 'bounty.apiToken';
  const RADAR_URL = '/dashboard/api/investing/radar';
  const SOCIAL_PULSE_URL = '/dashboard/api/investing/social-pulse';
  const PRIVATE_RADAR_URL = '/dashboard/api/investing/private-radar';
  const PRIVATE_SCAN_URL = '/dashboard/api/investing/private-radar/scans';
  const DEFAULT_RADAR_URL = '/dashboard/api/investing/radar?limit=40&country=&category=';
  const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

  const state = {
    country: '',
    category: '',
    lastPayload: null,
    pollingRunId: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function append(parent, ...children) {
    children.flat().filter(Boolean).forEach(child => parent.append(child));
    return parent;
  }

  function getToken() {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  }

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

  function clearError() {
    const node = $('#global-error');
    node.textContent = '';
    node.classList.add('hidden');
  }

  async function api(url, options = {}, retried = false) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const token = getToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);

    let response;
    try {
      response = await fetch(url, { ...options, headers });
    } catch (error) {
      throw new Error(`Network unavailable: ${error.message}`);
    }

    if (response.status === 401 && !retried) {
      const entered = window.prompt('This dashboard requires an API bearer token. It stays in sessionStorage for this browser tab.');
      if (entered && entered.trim()) {
        sessionStorage.setItem(TOKEN_KEY, entered.trim());
        return api(url, options, true);
      }
    }

    if (!response.ok) {
      let detail = response.status === 401 ? 'Authentication required' : `Request failed (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') detail = body.detail;
        else if (body.detail && typeof body.detail === 'object') {
          detail = body.detail.message || body.detail.error || detail;
        }
      } catch (_) { /* The response did not include JSON error detail. */ }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }

    if (response.status === 204) return null;
    return response.json();
  }

  function radarUrl() {
    if (!state.country && !state.category) return DEFAULT_RADAR_URL;
    const query = new URLSearchParams();
    query.set('limit', '40');
    query.set('country', state.country);
    query.set('category', state.category);
    return `${RADAR_URL}?${query.toString()}`;
  }

  function classicTopicUrl(keyword) {
    return `/dashboard/classic?topic=${encodeURIComponent(String(keyword || ''))}`;
  }

  function showView(name, updateHistory = true) {
    $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
    $$('.nav-item').forEach(item => {
      const active = item.dataset.view === name;
      item.classList.toggle('active', active);
      if (active) item.setAttribute('aria-current', 'page');
      else item.removeAttribute('aria-current');
    });
    if (updateHistory) history.replaceState(null, '', `#${name}`);
    $('#investing-desk').focus({ preventScroll: true });
  }

  function statePanel(kicker, title, copy, className = '') {
    const panel = element('div', `state-panel ${className}`.trim());
    append(panel, element('p', 'eyebrow', kicker), element('h3', '', title), element('p', '', copy));
    return panel;
  }

  function parseTimestamp(value) {
    if (!value) return null;
    if (value instanceof Date) {
      return Number.isNaN(value.getTime()) ? null : value;
    }
    if (typeof value === 'object') {
      const candidate = value.completed_at || value.finished_at || value.ended_at || value.swept_at || value.observed_at || value.latest_observed_at || value.updated_at || value.created_at || value.started_at || value.timestamp;
      return parseTimestamp(candidate);
    }
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatTimestamp(value) {
    const parsed = parseTimestamp(value);
    if (!parsed) return 'Timestamp not reported';
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(parsed);
  }

  function formatInteger(value) {
    if (value === null || value === undefined || value === '') return 'Not reported';
    const number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat().format(number) : String(value);
  }

  function formatGrowth(value) {
    if (value === null || value === undefined || value === '') return 'Not reported';
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    const sign = number > 0 ? '+' : '';
    return `${sign}${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(number)}%`;
  }

  function formatStarted(value) {
    if (value === null || value === undefined || value === '') return 'Not reported';
    const hours = Number(value);
    if (!Number.isFinite(hours)) return String(value);
    if (hours < 1) return 'Less than 1 hour ago';
    const rounded = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(hours);
    return `${rounded} ${hours === 1 ? 'hour' : 'hours'} ago`;
  }

  function asArray(value) {
    if (Array.isArray(value)) return value.filter(item => item !== null && item !== undefined && item !== '');
    if (value === null || value === undefined || value === '') return [];
    return [value];
  }

  function readableValue(value) {
    if (value && typeof value === 'object') {
      return String(value.name || value.label || value.code || value.value || '');
    }
    return String(value || '');
  }

  function readableList(value) {
    const entries = asArray(value).map(readableValue).filter(Boolean);
    return entries.length ? entries.join(' · ') : 'Not reported';
  }

  function countrySummary(value) {
    const entries = asArray(value).map(readableValue).filter(Boolean);
    if (!entries.length) return 'Not reported';
    const visible = entries.slice(0, 5);
    const remaining = entries.length - visible.length;
    return remaining > 0
      ? `${visible.join(' · ')} · +${remaining} markets`
      : visible.join(' · ');
  }

  function metricScopeName(item) {
    const code = String(item?.metric_scope_country || '');
    if (!code) return '';
    const match = asArray(item?.countries).find(country => String(country?.code || '') === code);
    return readableValue(match) || code;
  }

  function sourceName(value) {
    if (value && typeof value === 'object') {
      return String(value.name || value.label || value.provider || value.id || 'Source not reported');
    }
    return value ? String(value) : 'Source not reported';
  }

  function metric(label, value) {
    const wrapper = element('div', 'metric');
    append(wrapper, element('dt', '', label), element('dd', '', value));
    return wrapper;
  }

  function signalRow(item, index) {
    const article = element('article', 'signal-row');
    if (item && item.id !== null && item.id !== undefined) article.dataset.signalId = String(item.id);

    const rank = element('div', 'signal-rank mono', String(index + 1).padStart(2, '0'));
    rank.setAttribute('aria-hidden', 'true');

    const body = element('div', 'signal-body');
    const heading = element('div', 'signal-heading');
    const title = element('h3', '', item?.keyword || 'Keyword not reported');
    const investigate = element('a', 'investigate-link', 'Investigate');
    investigate.href = classicTopicUrl(item?.keyword || '');
    investigate.setAttribute('aria-label', `Investigate ${item?.keyword || 'this signal'} in Classic Bounty`);
    append(heading, title, investigate);

    const taxonomy = element('p', 'taxonomy');
    taxonomy.textContent = `${readableList(item?.categories)}  /  ${countrySummary(item?.countries)}`;

    const reasons = element('div', 'signal-reasons');
    const reasonLabel = element('p', 'field-label', 'Why it is on the radar');
    const reasonValues = asArray(item?.reasons).map(readableValue).filter(Boolean);
    append(reasons, reasonLabel);
    if (reasonValues.length) {
      const list = element('ul');
      reasonValues.forEach(reason => list.append(element('li', '', reason)));
      reasons.append(list);
    } else {
      reasons.append(element('p', 'not-reported', 'No reason was reported by the source.'));
    }

    const metrics = element('dl', 'signal-metrics');
    const metricScope = metricScopeName(item);
    const scopedLabel = label => metricScope ? `${label} · ${metricScope}` : label;
    append(
      metrics,
      metric(scopedLabel('Search volume'), formatInteger(item?.search_volume)),
      metric(scopedLabel('Growth'), formatGrowth(item?.growth_pct)),
      metric(scopedLabel('Started'), formatStarted(item?.started_hours_ago)),
    );

    const receipt = element('div', 'source-receipt');
    append(
      receipt,
      element('span', 'source-name', sourceName(item?.source)),
      element('span', 'source-time', `Observed ${formatTimestamp(item?.latest_observed_at)}`),
    );

    append(body, heading, taxonomy, reasons, metrics, receipt);
    append(article, rank, body);
    return article;
  }

  function platformLabel(value) {
    const labels = { reddit: 'Reddit', youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', x: 'X' };
    return labels[String(value || '').toLowerCase()] || String(value || 'Unknown source');
  }

  function supportLabel(value) {
    const labels = {
      cross_platform: 'Cross-platform',
      repeated_voices: 'Repeated voices',
      single_source_early: 'Single-source early lead',
    };
    return labels[value] || 'Support not classified';
  }

  function socialSignalRow(item, index) {
    const article = element('article', 'signal-row social-signal-row');
    const rank = element('div', 'signal-rank mono', String(index + 1).padStart(2, '0'));
    rank.setAttribute('aria-hidden', 'true');
    const body = element('div', 'signal-body');
    const heading = element('div', 'signal-heading');
    const title = element('h3', '', item?.label || 'Subject not reported');
    const investigate = element('a', 'investigate-link', 'Investigate');
    investigate.href = classicTopicUrl(item?.label || '');
    investigate.setAttribute('aria-label', `Investigate ${item?.label || 'this subject'} in Classic Bounty`);
    append(heading, title, investigate);

    const taxonomy = element('p', 'taxonomy');
    taxonomy.textContent = `${String(item?.behaviour_type || 'other').replaceAll('_', ' ')}  /  ${supportLabel(item?.support_type)}  /  ${readableList(asArray(item?.platforms).map(platformLabel))}`;
    const summary = element('p', 'social-summary', item?.summary || 'No social summary was returned.');
    const reasons = element('div', 'signal-reasons');
    append(
      reasons,
      element('p', 'field-label', 'Why consider it'),
      element('p', 'social-reason', item?.why_investigate || 'The extraction did not provide an investigation reason.'),
    );

    const metrics = element('dl', 'signal-metrics social-metrics');
    append(
      metrics,
      metric('Independent voices', formatInteger(item?.voice_count)),
      metric('Platforms', formatInteger(item?.platform_count)),
      metric('Evidence records', formatInteger(asArray(item?.evidence).length)),
    );

    const evidence = element('div', 'social-evidence');
    evidence.append(element('p', 'field-label', 'Source evidence'));
    const list = element('ul', 'social-evidence-list');
    asArray(item?.evidence).slice(0, 5).forEach(record => {
      const row = element('li');
      const link = element('a', 'source-link', platformLabel(record?.platform));
      link.href = record?.url || '#';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const text = String(record?.text || '').replace(/\s+/g, ' ').trim();
      append(row, link, element('span', '', text.length > 180 ? `${text.slice(0, 177)}...` : text));
      list.append(row);
    });
    if (!list.children.length) list.append(element('li', 'not-reported', 'No openable source evidence was returned.'));
    evidence.append(list);

    append(body, heading, taxonomy, summary, reasons, metrics, evidence);
    append(article, rank, body);
    return article;
  }

  function privateRadarRow(item, index) {
    const article = element('article', 'signal-row social-signal-row');
    article.dataset.signalId = String(item?.candidate_id || '');
    const rank = element('div', 'signal-rank mono', String(index + 1).padStart(2, '0'));
    const body = element('div', 'signal-body');
    const heading = element('div', 'signal-heading');
    const title = element('h3', '', item?.label || 'Qualified subject');
    const investigate = element('a', 'investigate-link', 'Investigate');
    investigate.href = classicTopicUrl(item?.label || '');
    append(heading, title, investigate);

    const parity = item?.parity?.level || 'Unknown parity';
    const taxonomy = element('p', 'taxonomy');
    taxonomy.textContent = `Retrospective anomaly  /  ${String(item?.behaviour_type || '').replaceAll('_', ' ')}  /  ${parity}  /  ${readableList(asArray(item?.platforms).map(platformLabel))}`;
    const summary = element('p', 'social-summary', `Hypothesis: ${item?.summary || 'No hypothesis reported.'}`);

    const details = element('div', 'signal-reasons');
    const fields = [
      ['Possible economic mechanism', item?.economic_mechanism],
      ['Question to investigate', item?.why_investigate],
      ['Counterevidence to check', item?.contradiction],
      ['Invalidation test', item?.invalidation],
    ];
    fields.forEach(([label, value]) => {
      append(details, element('p', 'field-label', label), element('p', 'social-reason', value || 'Not reported'));
    });

    const currentWindow = asArray(item?.windows).find(window => window?.window_key === 'current') || {};
    const metrics = element('dl', 'signal-metrics social-metrics');
    append(
      metrics,
      metric('Independent voices', formatInteger(item?.voice_count)),
      metric('Platforms', formatInteger(asArray(item?.platforms).length)),
      metric('Current X sample', formatInteger(currentWindow?.result_count)),
      metric('Evidence records', formatInteger(asArray(item?.evidence).length)),
    );

    const evidence = element('div', 'social-evidence');
    evidence.append(element('p', 'field-label', 'Source evidence'));
    const list = element('ul', 'social-evidence-list');
    asArray(item?.evidence).slice(0, 8).forEach(record => {
      const row = element('li');
      const link = element('a', 'source-link', platformLabel(record?.platform));
      link.href = record?.url || '#';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const text = String(record?.text || '').replace(/\s+/g, ' ').trim();
      append(row, link, element('span', '', text.length > 180 ? `${text.slice(0, 177)}...` : text));
      list.append(row);
    });
    evidence.append(list);
    append(body, heading, taxonomy, summary, details, metrics, evidence);
    append(article, rank, body);
    return article;
  }

  function privateCoverageText(payload) {
    const coverage = payload?.coverage || {};
    const prefix = payload?.displaying_previous_data ? 'Showing earlier qualified data · ' : '';
    return `${prefix}${coverage.summary || 'No private scan coverage reported'}`;
  }

  function renderPrivateRadar(payload) {
    const safe = payload && typeof payload === 'object' ? payload : {};
    const items = asArray(safe.items);
    const attempt = safe.last_attempt;
    const dataScan = safe.data_scan;
    const list = $('#social-list');
    list.replaceChildren();
    list.setAttribute('aria-busy', 'false');
    $('#social-coverage').textContent = privateCoverageText(safe);
    $('#coverage-status').textContent = privateCoverageText(safe);
    $('#sweep-status').textContent = attempt
      ? `${String(attempt.status || 'unknown').replaceAll('_', ' ')} · ${formatTimestamp(attempt.completed_at || attempt.started_at)}`
      : 'No private scan yet';
    $('#stale-notice').classList.add('hidden');

    if (attempt?.status === 'running') {
      $('#social-status').textContent = `${String(attempt.stage || 'running').replaceAll('_', ' ')} · ${formatInteger(attempt.progress)}%`;
    } else if (attempt?.status === 'failed') {
      $('#social-status').textContent = 'Latest scan failed';
    } else if (dataScan?.status === 'no_qualified_leads') {
      $('#social-status').textContent = 'No qualified leads';
    } else if (dataScan) {
      $('#social-status').textContent = `Qualified scan · ${formatTimestamp(dataScan.completed_at || dataScan.started_at)}`;
    } else {
      $('#social-status').textContent = 'Awaiting first private scan';
    }

    if (!items.length) {
      const running = attempt?.status === 'running';
      list.append(statePanel(
        running ? 'Scanning' : attempt?.status === 'failed' ? 'Failed' : 'No qualified leads',
        running ? 'Owned sources are being checked' : attempt?.status === 'failed' ? 'The latest scan did not complete' : 'Nothing passed every investment gate',
        running
          ? 'Historical windows, behavior evidence, breadth, citations, and information parity are checked before anything appears.'
          : 'An empty result is valid. Raw posts and generic trends are not used as filler.',
        running ? 'loading-state' : attempt?.status === 'failed' ? 'failed-state' : 'empty-state',
      ));
      return;
    }
    items.forEach((item, index) => list.append(privateRadarRow(item, index)));
  }

  async function loadPrivateRadar() {
    const list = $('#social-list');
    list.setAttribute('aria-busy', 'true');
    try {
      const payload = await api(PRIVATE_RADAR_URL);
      renderPrivateRadar(payload);
      if (payload?.last_attempt?.status === 'running') pollPrivateScan(payload.last_attempt.id);
      return payload;
    } catch (error) {
      list.setAttribute('aria-busy', 'false');
      list.replaceChildren(statePanel('Failed', 'Private Radar unavailable', error.message, 'failed-state'));
      showError(`Private Radar could not be loaded: ${error.message}`);
      return null;
    }
  }

  async function pollPrivateScan(runId) {
    if (!runId || state.pollingRunId === runId) return;
    state.pollingRunId = runId;
    try {
      for (let attempt = 0; attempt < 600; attempt += 1) {
        const payload = await api(`${PRIVATE_SCAN_URL}/${encodeURIComponent(runId)}`);
        const scan = payload?.scan || {};
        $('#social-status').textContent = `${String(scan.stage || scan.status || 'running').replaceAll('_', ' ')} · ${formatInteger(scan.progress)}%`;
        if (scan.status !== 'running') {
          await loadPrivateRadar();
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
      showError('Private scan is still running. Reload later to check persisted progress.');
    } catch (error) {
      showError(`Private scan status failed: ${error.message}`);
    } finally {
      state.pollingRunId = null;
      $('#reload-radar').disabled = false;
    }
  }

  async function startPrivateScan() {
    const button = $('#reload-radar');
    button.disabled = true;
    clearError();
    try {
      const response = await api(PRIVATE_SCAN_URL, { method: 'POST' });
      toast(response.started ? 'Private scan started' : 'Existing private scan resumed');
      await loadPrivateRadar();
      pollPrivateScan(response.run_id);
    } catch (error) {
      button.disabled = false;
      showError(`Private scan could not start: ${error.message}`);
    }
  }

  function socialCoverageText(coverage) {
    if (!coverage || typeof coverage !== 'object') return 'Social coverage not reported';
    const sources = asArray(coverage.sources);
    const prefix = coverage.displaying_previous_data ? 'Showing previous supported pulse · ' : '';
    if (!sources.length) return `${prefix}${coverage.summary || 'No social sources checked'}`;
    const details = sources.map(source => `${platformLabel(source.platform)}: ${String(source.status || 'unknown').replaceAll('_', ' ')}`);
    return `${prefix}${coverage.summary || 'Social sources checked'} · ${details.join(' · ')}`;
  }

  function renderSocialPulse(payload) {
    const safePayload = payload && typeof payload === 'object' ? payload : {};
    const items = asArray(safePayload.items);
    const list = $('#social-list');
    list.replaceChildren();
    list.setAttribute('aria-busy', 'false');
    $('#social-coverage').textContent = socialCoverageText(safePayload.coverage);
    const dataRun = safePayload.data_run;
    const attempt = safePayload.last_attempt;
    const fallbackMode = items.some(item => item?.extraction_mode === 'deterministic_fallback');
    if (fallbackMode) {
      $('#social-status').textContent = 'Source leads · synthesis unavailable';
      $('#social-coverage').textContent = `Model synthesis unavailable; showing source-native leads · ${$('#social-coverage').textContent}`;
    } else if (attempt?.status === 'analysis_unavailable') {
      $('#social-status').textContent = 'Analysis unavailable';
    } else if (dataRun) {
      const dataStamp = formatTimestamp(dataRun.completed_at || dataRun.started_at);
      $('#social-status').textContent = attempt && attempt.id !== dataRun.id
        ? `Earlier supported pulse · ${dataStamp}`
        : `Persisted social pulse · ${dataStamp}`;
    } else {
      $('#social-status').textContent = 'Awaiting first supported pulse';
    }
    if (!items.length) {
      list.append(statePanel(
        attempt?.status === 'analysis_unavailable' ? 'Analysis unavailable' : 'No supported leads yet',
        attempt?.status === 'analysis_unavailable' ? 'Social records could not be interpreted' : 'The first social discovery run has not produced a supported subject',
        'Source gaps and empty results remain visible above. Collection runs centrally and this page never invents social leads.',
        attempt?.status === 'analysis_unavailable' ? 'failed-state' : 'empty-state',
      ));
      return;
    }
    items.slice(0, 12).forEach((item, index) => list.append(socialSignalRow(item, index)));
  }

  async function loadSocialPulse() {
    const list = $('#social-list');
    list.setAttribute('aria-busy', 'true');
    try {
      renderSocialPulse(await api(SOCIAL_PULSE_URL));
    } catch (error) {
      list.setAttribute('aria-busy', 'false');
      list.replaceChildren(statePanel('Failed', 'Social Pulse unavailable', error.message, 'failed-state'));
      $('#social-status').textContent = 'Unavailable';
      $('#social-coverage').textContent = 'Social coverage unavailable';
    }
  }

  function quietLaneItems(payload) {
    const direct = payload?.building_quietly;
    const nested = payload?.lanes?.building_quietly;
    if (Array.isArray(direct)) return direct;
    if (Array.isArray(nested)) return nested;
    if (Array.isArray(direct?.items)) return direct.items;
    if (Array.isArray(nested?.items)) return nested.items;
    return asArray(payload?.items).filter(item => String(item?.lane || '').toLowerCase() === 'building_quietly');
  }

  function breakingLaneItems(payload, quietItems) {
    const direct = payload?.breaking_now;
    const nested = payload?.lanes?.breaking_now;
    if (Array.isArray(direct)) return direct;
    if (Array.isArray(nested)) return nested;
    if (Array.isArray(direct?.items)) return direct.items;
    if (Array.isArray(nested?.items)) return nested.items;
    const quietIds = new Set(quietItems.map(item => item?.id).filter(value => value !== null && value !== undefined));
    return asArray(payload?.items).filter(item => {
      if (String(item?.lane || '').toLowerCase() === 'building_quietly') return false;
      return item?.id === null || item?.id === undefined || !quietIds.has(item.id);
    });
  }

  function renderSignalList(target, items, emptyTitle, emptyCopy) {
    target.replaceChildren();
    if (!items.length) {
      target.append(statePanel('Empty', emptyTitle, emptyCopy, 'empty-state'));
      return;
    }
    items.forEach((item, index) => target.append(signalRow(item, index)));
  }

  function optionValues(value) {
    return asArray(value).map(entry => {
      if (entry && typeof entry === 'object') {
        const optionValue = entry.code || entry.value || entry.id || entry.name || entry.label;
        const optionLabel = entry.name || entry.label || entry.code || entry.value || entry.id;
        return optionValue && optionLabel ? { value: String(optionValue), label: String(optionLabel) } : null;
      }
      return entry ? { value: String(entry), label: String(entry) } : null;
    }).filter(Boolean);
  }

  function populateFilter(select, choices, selected) {
    const existing = new Set([...select.options].map(option => option.value));
    optionValues(choices).forEach(choice => {
      if (existing.has(choice.value)) return;
      const option = document.createElement('option');
      option.value = choice.value;
      option.textContent = choice.label;
      select.append(option);
      existing.add(choice.value);
    });
    select.value = existing.has(selected) ? selected : '';
  }

  function populateCoverageFilters(coverage) {
    if (!coverage || typeof coverage !== 'object' || Array.isArray(coverage)) return;
    populateFilter(
      $('#country-filter'),
      coverage.country_options || coverage.countries || coverage.available_countries,
      state.country,
    );
    populateFilter(
      $('#category-filter'),
      coverage.category_options || coverage.categories || coverage.available_categories,
      state.category,
    );
  }

  function formatCoverage(coverage) {
    if (coverage === null || coverage === undefined || coverage === '') return 'Coverage not reported by API';
    if (typeof coverage !== 'object') return String(coverage);
    if (Array.isArray(coverage)) return readableList(coverage);
    if (coverage.summary || coverage.label) return String(coverage.summary || coverage.label);

    const details = [];
    const countries = asArray(coverage.countries || coverage.country_options || coverage.available_countries);
    const categories = asArray(coverage.categories || coverage.category_options || coverage.available_categories);
    const itemCount = coverage.item_count ?? coverage.items;
    if (countries.length) details.push(`${countries.length} ${countries.length === 1 ? 'market' : 'markets'}`);
    if (categories.length) details.push(`${categories.length} ${categories.length === 1 ? 'category' : 'categories'}`);
    if (itemCount !== null && itemCount !== undefined && !Array.isArray(itemCount)) details.push(`${formatInteger(itemCount)} signals`);
    return details.length ? details.join(' · ') : 'Coverage reported without a summary';
  }

  function sweepStatusText(lastSweep) {
    if (!lastSweep || typeof lastSweep !== 'object') return 'No source sweep reported';
    const status = String(lastSweep.status || 'unknown').replaceAll('_', ' ');
    const timestamp = formatTimestamp(lastSweep.completed_at || lastSweep.started_at);
    return `${status} · ${timestamp}`;
  }

  function updateFreshness(dataSweep, items, dataObservedAt) {
    const explicitDataDate = parseTimestamp(dataObservedAt);
    const latestItemDate = items
      .map(item => parseTimestamp(item?.latest_observed_at))
      .filter(Boolean)
      .sort((a, b) => b.getTime() - a.getTime())[0] || null;
    const freshnessDate = explicitDataDate || latestItemDate || parseTimestamp(dataSweep);
    const stale = freshnessDate && Date.now() - freshnessDate.getTime() > STALE_AFTER_MS;
    $('#stale-notice').classList.toggle('hidden', !stale);
  }

  function renderRadar(payload) {
    const safePayload = payload && typeof payload === 'object' ? payload : {};
    const quietItems = quietLaneItems(safePayload);
    const breakingItems = breakingLaneItems(safePayload, quietItems);
    const allItems = [...breakingItems, ...quietItems];

    state.lastPayload = safePayload;
    populateCoverageFilters(safePayload.coverage);
    $('#sweep-status').textContent = sweepStatusText(safePayload.last_sweep);
    $('#coverage-status').textContent = formatCoverage(safePayload.coverage);
    updateFreshness(
      safePayload.data_sweep,
      allItems,
      safePayload.data_observed_at,
    );

    renderSignalList(
      $('#breaking-list'),
      breakingItems,
      'No signals in this scope',
      'The persisted radar returned no breaking signals for these filters. Reset to global or request a refresh.',
    );
    $('#breaking-list').setAttribute('aria-busy', 'false');

    if (quietItems.length) {
      $('#building-status').textContent = 'Persisted feed';
      $('#building-status').classList.remove('development');
      renderSignalList(
        $('#building-list'),
        quietItems,
        'No quiet-build signals in this scope',
        'The API returned a quiet-build lane but no signals matched these filters.',
      );
    } else {
      $('#building-status').textContent = 'In development';
      $('#building-status').classList.add('development');
      $('#building-list').replaceChildren(statePanel(
        'In development',
        'No quiet-build feed is available yet',
        'This lane stays explicitly empty until the API returns persisted data for it.',
        'development-state',
      ));
    }
  }

  async function loadRadar() {
    const list = $('#breaking-list');
    clearError();
    list.setAttribute('aria-busy', 'true');
    list.replaceChildren(statePanel('Loading', 'Checking the persisted radar', 'The radar request is in progress.', 'loading-state'));
    try {
      const payload = await api(radarUrl());
      renderRadar(payload);
    } catch (error) {
      list.setAttribute('aria-busy', 'false');
      list.replaceChildren(statePanel(
        'Failed',
        'Radar unavailable',
        `${error.message}. Persisted signals could not be loaded.`,
        'failed-state',
      ));
      $('#sweep-status').textContent = 'Source sweep unavailable';
      $('#coverage-status').textContent = 'Coverage unavailable';
      $('#stale-notice').classList.add('hidden');
      showError(`Radar could not be loaded: ${error.message}`);
    }
  }

  async function reloadRadar() {
    const button = $('#reload-radar');
    button.disabled = true;
    try {
      await Promise.all([loadRadar(), loadSocialPulse()]);
      toast('Persisted radar reloaded');
    } finally {
      button.disabled = false;
    }
  }

  function bindEvents() {
    $$('.nav-item').forEach(item => item.addEventListener('click', () => showView(item.dataset.view)));

    $('#radar-filters').addEventListener('submit', event => {
      event.preventDefault();
      state.country = $('#country-filter').value;
      state.category = $('#category-filter').value;
      loadRadar();
    });

    $('#clear-filters').addEventListener('click', () => {
      state.country = '';
      state.category = '';
      $('#country-filter').value = '';
      $('#category-filter').value = '';
      loadRadar();
    });

    $('#reload-radar').addEventListener('click', startPrivateScan);

    $('#set-token').addEventListener('click', () => {
      const entered = window.prompt('API bearer token. Leave blank to clear this tab’s token.', getToken());
      if (entered === null) return;
      if (entered.trim()) {
        sessionStorage.setItem(TOKEN_KEY, entered.trim());
        toast('API token saved for this tab');
        loadPrivateRadar();
      } else {
        sessionStorage.removeItem(TOKEN_KEY);
        toast('API token cleared');
      }
    });

    window.addEventListener('hashchange', () => {
      const requested = window.location.hash.slice(1);
      const view = ['radar', 'research', 'monitors', 'usage'].includes(requested) ? requested : 'radar';
      showView(view, false);
    });
  }

  function init() {
    bindEvents();
    const requested = window.location.hash.slice(1);
    showView(['radar', 'research', 'monitors', 'usage'].includes(requested) ? requested : 'radar', false);
    loadPrivateRadar();
  }

  init();
})();
