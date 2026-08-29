(() => {
  'use strict';

  const TOKEN_KEY = 'bounty.apiToken';
  const RADAR_URL = '/dashboard/api/investing/radar';
  const SOCIAL_PULSE_URL = '/dashboard/api/investing/social-pulse';
  const PRIVATE_RADAR_URL = '/dashboard/api/investing/private-radar';
  const PRIVATE_SCAN_URL = '/dashboard/api/investing/private-radar/scans';
  const PRIVATE_SNAPSHOT_URL = '/private-radar-snapshot.json';
  const INVESTMENT_DOSSIER_RUNS_URL = '/dashboard/api/investing/dossier-runs';
  const READ_ONLY_SNAPSHOT = new URLSearchParams(window.location.search).get('snapshot') === '1';
  const DEFAULT_RADAR_URL = '/dashboard/api/investing/radar?limit=40&country=&category=';
  const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

  const state = {
    country: '',
    category: '',
    lastPayload: null,
    lastPrivatePayload: null,
    movementGeo: 'WORLDWIDE',
    movementHorizon: '3m',
    movementQueries: {},
    pollingRunId: null,
    researchDraft: null,
    researchRuns: [],
    selectedResearchRunId: null,
    researchPollingRunId: null,
    researchLoaded: false,
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
    if (name === 'research' && !state.researchLoaded) loadInvestmentDossierRuns();
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
    if (hours >= 48) {
      const days = hours / 24;
      const roundedDays = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(days);
      return `${roundedDays} days ago`;
    }
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

  function safeSourceUrl(value) {
    try {
      const parsed = new URL(String(value || ''));
      return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
    } catch (_error) {
      return '';
    }
  }

  function engagementSummary(record) {
    const metrics = record?.engagement && typeof record.engagement === 'object'
      ? record.engagement
      : {};
    const parts = [];
    if (metrics.views !== null && metrics.views !== undefined && Number.isFinite(Number(metrics.views))) {
      parts.push(`${formatInteger(metrics.views)} views`);
    }
    if (metrics.likes !== null && metrics.likes !== undefined && Number.isFinite(Number(metrics.likes))) {
      parts.push(`${formatInteger(metrics.likes)} likes`);
    }
    const replies = Number(metrics.comments || 0) + Number(metrics.replies || 0);
    if (Number.isFinite(replies) && replies > 0) parts.push(`${formatInteger(replies)} comments/replies`);
    return parts.length ? parts.join(' · ') : 'Engagement not captured in this scan';
  }

  function sourceLinkLabel(record) {
    if (record?.platform === 'x' && String(record?.url || '').includes('platform.twitter.com/embed/')) {
      return 'X capture';
    }
    return platformLabel(record?.platform);
  }

  function movementItemKey(item) {
    return String(
      item?.candidate_id || item?.normalized_keyword || item?.keyword || item?.label || 'movement',
    );
  }

  function trajectoryHasUsableMovement(trajectory) {
    if (!trajectory || trajectory.status !== 'complete') return false;
    const points = asArray(trajectory.points).filter(point => Number.isFinite(Number(point?.value)));
    return points.length >= 30 && points.filter(point => Number(point.value) > 0).length >= 8;
  }

  function movementOptionUsable(option, bundle) {
    const selectedGeo = state.movementGeo || String(bundle?.default_geo || 'WORLDWIDE');
    const selectedHorizon = state.movementHorizon || String(bundle?.default_horizon || '3m');
    return trajectoryHasUsableMovement(
      option?.series?.[selectedGeo]?.[selectedHorizon],
    );
  }

  function movementQueryOptions(bundle) {
    const options = asArray(bundle?.query_options).filter(option => (
      option && typeof option === 'object' && option.query
    ));
    if (options.length) return options;
    if (bundle?.query) {
      return [{
        query: String(bundle.query),
        reason: 'Primary query selected for this subject.',
        source: 'selected_query',
        series: bundle.series || {},
        classification: bundle.classification || null,
      }];
    }
    return [];
  }

  function selectedMovementOption(item, bundle) {
    const options = movementQueryOptions(bundle);
    const usable = options.filter(option => movementOptionUsable(option, bundle));
    const preferred = state.movementQueries[movementItemKey(item)]
      || String(bundle?.default_query || bundle?.query || '');
    return usable.find(option => String(option.query) === preferred)
      || usable.find(option => String(option.query) === String(bundle?.default_query || ''))
      || usable[0]
      || null;
  }

  function movementTrajectory(item) {
    const bundle = item?.movement_bundle && typeof item.movement_bundle === 'object'
      ? item.movement_bundle
      : {};
    const selectedGeo = state.movementGeo || String(bundle.default_geo || 'WORLDWIDE');
    const selectedHorizon = state.movementHorizon || String(bundle.default_horizon || '3m');
    const option = selectedMovementOption(item, bundle);
    if (option?.series && typeof option.series === 'object') {
      const selected = option?.series?.[selectedGeo]?.[selectedHorizon];
      return selected && typeof selected === 'object' ? selected : {};
    }
    const hasBundleSeries = bundle.series && typeof bundle.series === 'object';
    const bundledTrajectory = bundle?.series?.[selectedGeo]?.[selectedHorizon];
    if (hasBundleSeries) {
      return bundledTrajectory && typeof bundledTrajectory === 'object'
        ? bundledTrajectory
        : {};
    }
    return item?.trajectory && typeof item.trajectory === 'object'
      ? item.trajectory
      : {};
  }

  function hasSelectedMovement(item) {
    return trajectoryHasUsableMovement(movementTrajectory(item));
  }

  function movementPanel(item) {
    const bundle = item?.movement_bundle && typeof item.movement_bundle === 'object'
      ? item.movement_bundle
      : {};
    const selectedGeo = state.movementGeo || String(bundle.default_geo || 'WORLDWIDE');
    const selectedHorizon = state.movementHorizon || String(bundle.default_horizon || '3m');
    const queryOptions = movementQueryOptions(bundle);
    const selectedOption = selectedMovementOption(item, bundle);
    const trajectory = movementTrajectory(item);
    const points = asArray(trajectory.points).filter(point => Number.isFinite(Number(point?.value)));
    const panel = element('section', 'movement-panel');
    panel.append(element('p', 'field-label', 'Search movement'));
    if (queryOptions.length > 1) {
      const picker = element('div', 'movement-query-options');
      picker.setAttribute('role', 'group');
      picker.setAttribute('aria-label', 'Google Trends query');
      queryOptions.forEach(option => {
        const active = option === selectedOption;
        const usable = movementOptionUsable(option, bundle);
        const button = element(
          'button',
          `movement-query-option${active ? ' active' : ''}${usable ? '' : ' unavailable'}`,
          option.query,
        );
        button.type = 'button';
        button.disabled = !usable;
        button.title = usable
          ? option.reason || 'Show this query'
          : 'No comparable series for this market and timeframe';
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
        button.addEventListener('click', () => {
          if (!usable) return;
          state.movementQueries[movementItemKey(item)] = String(option.query);
          if (state.lastPrivatePayload) renderPrivateRadar(state.lastPrivatePayload);
        });
        picker.append(button);
      });
      panel.append(picker);
      panel.append(element(
        'p',
        'movement-query-caveat',
        'Queries are tested separately. Each chart has its own 0–100 scale, so compare shape and persistence, not line height. Dashed options lack usable history for this selection.',
      ));
    }
    panel.append(element(
      'p',
      'movement-query',
      `Query shown: “${selectedOption?.query || trajectory.query || item?.label || 'Not reported'}”`,
    ));
    const queryReason = selectedOption?.reason || item?.trajectory_query_reason;
    if (queryReason) {
      panel.append(element('p', 'movement-query-reason', queryReason));
    }
    if (points.length < 2) {
      panel.append(element(
        'p',
        'movement-unavailable',
        'No comparable movement series was collected. This subject cannot enter the watchlist.',
      ));
      return panel;
    }
    const geoName = asArray(bundle.geographies).find(value => value?.code === selectedGeo)?.name
      || (selectedGeo === 'WORLDWIDE' ? 'Worldwide' : selectedGeo || 'Worldwide');
    const horizonName = asArray(bundle.horizons).find(value => value?.code === selectedHorizon)?.name
      || selectedHorizon;
    panel.append(element('p', 'movement-scope', `${geoName} · ${horizonName}`));
    const chartWidth = 640;
    const chartHeight = 148;
    const chartLeft = 30;
    const chartRight = 632;
    const chartTop = 12;
    const chartBottom = 132;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'movement-chart');
    svg.setAttribute('viewBox', `0 0 ${chartWidth} ${chartHeight}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Google search interest for ${trajectory.query || item?.label || 'this subject'}`);
    [0, 50, 100].forEach(value => {
      const y = chartBottom - (value / 100) * (chartBottom - chartTop);
      const guide = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      guide.setAttribute('class', 'movement-guide');
      guide.setAttribute('x1', String(chartLeft));
      guide.setAttribute('x2', String(chartRight));
      guide.setAttribute('y1', String(y));
      guide.setAttribute('y2', String(y));
      svg.append(guide);
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('class', 'movement-y-label');
      label.setAttribute('x', '1');
      label.setAttribute('y', String(y + 4));
      label.textContent = String(value);
      svg.append(label);
    });
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('class', 'movement-line');
    line.setAttribute('fill', 'none');
    line.setAttribute('points', points.map((point, index) => {
      const x = chartLeft + (index / (points.length - 1)) * (chartRight - chartLeft);
      const y = chartBottom - (Math.max(0, Math.min(100, Number(point.value))) / 100) * (chartBottom - chartTop);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' '));
    svg.append(line);
    const axis = element('div', 'movement-axis');
    append(
      axis,
      element('span', '', String(points[0]?.date || 'Start')),
      element('span', '', '0–100 normalized'),
      element('span', '', String(points[points.length - 1]?.date || 'Latest')),
    );
    const values = points.map(point => Number(point.value));
    const latest = values[values.length - 1];
    const peak = Math.max(...values);
    const caption = element(
      'p',
      'movement-caption',
      `Google search interest · ${geoName} · ${horizonName} · latest ${formatInteger(latest)} · peak ${formatInteger(peak)}. Values are normalized 0–100 within this chart and are not social proof.`,
    );
    const classification = selectedOption?.classification && typeof selectedOption.classification === 'object'
      ? selectedOption.classification
      : bundle.classification && typeof bundle.classification === 'object'
        ? bundle.classification
        : null;
    const assessment = classification
      ? element(
          'p',
          `movement-assessment ${classification.trend_eligible ? 'eligible' : 'not-eligible'}`,
          `${String(classification.movement_type || 'unclear').replaceAll('_', ' ')}: ${classification.reason || 'No interpretation reported.'}`,
        )
      : null;
    append(panel, svg, axis, caption, assessment);
    return panel;
  }

  function evidenceContent(item, record) {
    const content = element('div', 'evidence-content');
    const text = String(record?.text || '').replace(/\s+/g, ' ').trim();
    const rawKind = String(item?.evidence_kinds?.[record?.id] || 'observation');
    const proofIds = asArray(item?.gates?.evidence_quality?.metrics?.proof_evidence_ids).map(String);
    const kind = rawKind === 'firsthand'
      ? proofIds.includes(String(record?.id)) ? 'firsthand behavior' : 'first-person context'
      : rawKind.replaceAll('_', ' ');
    append(
      content,
      element('span', 'evidence-text', text.length > 180 ? `${text.slice(0, 177)}...` : text),
      element('span', 'evidence-kind', kind),
      element('span', 'evidence-engagement', engagementSummary(record)),
    );
    return content;
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
      const link = element('a', 'source-link', sourceLinkLabel(record));
      link.href = safeSourceUrl(record?.url) || '#';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      append(row, link, evidenceContent(item, record));
      list.append(row);
    });
    if (!list.children.length) list.append(element('li', 'not-reported', 'No openable source evidence was returned.'));
    evidence.append(list);

    append(body, heading, taxonomy, summary, reasons, metrics, evidence);
    append(article, rank, body);
    return article;
  }

  function prepareInvestmentResearch(item) {
    if (READ_ONLY_SNAPSHOT) return;
    const scan = state.lastPrivatePayload?.review_scan || state.lastPrivatePayload?.data_scan || {};
    const candidateId = String(item?.candidate_id || '');
    const label = String(item?.label || '').trim();
    if (!scan.id || !candidateId || !label) {
      showError('This subject does not have a persisted candidate handoff.');
      return;
    }
    state.researchDraft = { item, sourceScanId: String(scan.id), candidateId };
    $('#research-source-scan').value = String(scan.id);
    $('#research-candidate-id').value = candidateId;
    $('#research-candidate-label').value = label;
    $('#research-company').value = String(item?.company_name || '');
    $('#research-ticker').value = String(item?.ticker || '');
    $('#research-exchange').value = String(item?.exchange_code || 'US');
    showView('research');
    $('#research-company').focus();
  }

  function investmentResearchButton(item) {
    const button = element('button', 'investigate-link research-action', 'Build dossier');
    button.type = 'button';
    button.setAttribute('aria-label', `Build a company dossier for ${item?.label || 'this subject'}`);
    button.addEventListener('click', () => prepareInvestmentResearch(item));
    if (READ_ONLY_SNAPSHOT) {
      button.disabled = true;
      button.title = 'Company research is unavailable in the read-only snapshot';
    }
    return button;
  }

  function privateRadarRow(item, index) {
    const article = element('article', 'signal-row social-signal-row');
    article.dataset.signalId = String(item?.candidate_id || '');
    const rank = element('div', 'signal-rank mono', String(index + 1).padStart(2, '0'));
    const body = element('div', 'signal-body');
    const heading = element('div', 'signal-heading');
    const title = element('h3', '', item?.label || 'Qualified subject');
    const actions = element('div', 'signal-actions');
    const investigate = element('a', 'investigate-link', 'Read conversations');
    investigate.href = classicTopicUrl(item?.label || '');
    append(actions, investigate, investmentResearchButton(item));
    append(heading, title, actions);

    const parity = item?.parity?.level || 'Unknown parity';
    const taxonomy = element('p', 'taxonomy');
    taxonomy.textContent = `Retrospective anomaly  /  ${String(item?.behaviour_type || '').replaceAll('_', ' ')}  /  ${parity}  /  ${readableList(asArray(item?.platforms).map(platformLabel))}`;
    const summary = element('p', 'social-summary', `Hypothesis: ${item?.summary || 'No hypothesis reported.'}`);
    const breadthMetrics = item?.gates?.breadth?.metrics || {};
    const singlePlatformCaveat = breadthMetrics.cross_platform === false
      ? element('p', 'qualification-caveat', 'Coverage caveat: independent support was observed on one platform only.')
      : null;

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
      const link = element('a', 'source-link', sourceLinkLabel(record));
      link.href = safeSourceUrl(record?.url) || '#';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      append(row, link, evidenceContent(item, record));
      list.append(row);
    });
    evidence.append(list);
    append(body, heading, taxonomy, summary, singlePlatformCaveat, movementPanel(item), details, metrics, evidence);
    append(article, rank, body);
    return article;
  }

  function reviewStatusCopy(value) {
    const copy = {
      search_movement_only: ['Search movement only', 'Search interest has a visible trajectory, but comparable social evidence is not yet strong enough for a lead.'],
      needs_more_evidence: ['Weak firsthand support', 'The checked posts did not reach the required number of independent firsthand voices with visible engagement.'],
      rejected: ['Rejected', 'The checked evidence did not support an emerging investment lead.'],
    };
    return copy[value] || ['Reviewed', 'The subject did not pass every promotion check.'];
  }

  function radarSectionHeading(kicker, title, copy) {
    const header = element('header', 'review-section-head');
    append(
      header,
      element('p', 'field-label', kicker),
      element('h3', '', title),
      element('p', '', copy),
    );
    return header;
  }

  function reviewRadarRow(item, index) {
    const status = String(item?.review_status || 'rejected');
    const [statusLabel, statusCopy] = reviewStatusCopy(status);
    const article = element('article', `signal-row social-signal-row review-row ${status}`);
    article.dataset.signalId = String(item?.candidate_id || '');
    const rank = element('div', 'signal-rank mono', String(index + 1).padStart(2, '0'));
    const body = element('div', 'signal-body');
    const heading = element('div', 'signal-heading');
    const title = element('h3', '', item?.label || 'Reviewed subject');
    const actions = element('div', 'signal-actions');
    const badge = element('span', 'review-badge', statusLabel);
    append(actions, badge, investmentResearchButton(item));
    append(heading, title, actions);

    const taxonomy = element('p', 'taxonomy');
    taxonomy.textContent = `${String(item?.behaviour_type || 'behavior not classified').replaceAll('_', ' ')}  /  ${readableList(asArray(item?.platforms).map(platformLabel))}`;
    const summary = element('p', 'social-summary', item?.summary || 'No hypothesis summary was returned.');
    const statusNote = element('p', 'review-status-copy', statusCopy);

    const blockers = element('div', 'review-blockers');
    blockers.append(element('p', 'field-label', 'Why it is not qualified'));
    const blockerList = element('ul', 'review-reason-list');
    asArray(item?.blocking_reasons).forEach(reason => blockerList.append(element('li', '', reason)));
    if (!blockerList.children.length) blockerList.append(element('li', '', 'The subject did not pass every promotion check.'));
    blockers.append(blockerList);

    const caveats = asArray(item?.caveats);
    if (caveats.length) {
      blockers.append(element('p', 'field-label', 'Data-quality notes'));
      const caveatList = element('ul', 'review-caveat-list');
      caveats.forEach(reason => caveatList.append(element('li', '', reason)));
      blockers.append(caveatList);
    }

    const question = element('div', 'signal-reasons');
    append(
      question,
      element('p', 'field-label', 'What is still worth investigating'),
      element('p', 'social-reason', item?.why_investigate || item?.economic_mechanism || 'No investigation question was reported.'),
    );

    const qualityMetrics = item?.gates?.evidence_quality?.metrics || {};
    const metrics = element('dl', 'signal-metrics social-metrics');
    append(
      metrics,
      metric('Firsthand voices', formatInteger(qualityMetrics.firsthand_authors)),
      metric('Engaged sources', formatInteger(qualityMetrics.engaged_records)),
      metric('Platforms', formatInteger(asArray(item?.platforms).length)),
      metric('Relevant evidence', formatInteger(asArray(item?.evidence).length)),
    );

    const evidence = element('div', 'social-evidence');
    evidence.append(element('p', 'field-label', 'Source evidence'));
    const sourceList = element('ul', 'social-evidence-list');
    asArray(item?.evidence).slice(0, 6).forEach(record => {
      const row = element('li');
      const link = element('a', 'source-link', sourceLinkLabel(record));
      link.href = safeSourceUrl(record?.url) || '#';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      append(row, link, evidenceContent(item, record));
      sourceList.append(row);
    });
    evidence.append(sourceList);

    append(body, heading, taxonomy, summary, statusNote, movementPanel(item), blockers, question, metrics, evidence);
    append(article, rank, body);
    return article;
  }

  function privateCoverageText(payload) {
    const coverage = payload?.coverage || {};
    const prefix = payload?.displaying_previous_data ? 'Showing earlier qualified data · ' : '';
    return `${prefix}${coverage.summary || 'No private scan coverage reported'}`;
  }

  function trendDiscoveryRow(item, index) {
    const article = element('article', 'signal-row trend-candidate-row');
    const rank = element('div', 'signal-rank mono', String(index + 1).padStart(2, '0'));
    const body = element('div', 'signal-body');
    const heading = element('div', 'signal-heading');
    const title = element('h3', '', item?.keyword || 'Search candidate');
    const badge = element('span', 'review-badge search-only', 'Search attention only');
    append(heading, title, badge);
    const taxonomy = element('p', 'taxonomy');
    taxonomy.textContent = `${readableList(item?.categories)}  /  ${readableList(item?.countries)}`;
    const context = item?.context && typeof item.context === 'object' ? item.context : {};
    const contextBlock = element('div', 'trend-context');
    const contextFields = [
      ['What it is', context.what_it_is],
      ['Why it may be rising', context.why_rising],
      ['Investing read', context.investing_angle],
    ];
    contextFields.forEach(([label, value]) => {
      if (!value) return;
      append(
        contextBlock,
        element('p', 'field-label', label),
        element('p', 'social-reason', value),
      );
    });
    const contextArticles = asArray(item?.context_articles);
    if (contextArticles.length) {
      contextBlock.append(element('p', 'field-label', 'Context sources'));
      const links = element('div', 'trend-context-links');
      contextArticles.slice(0, 3).forEach(articleValue => {
        const url = safeSourceUrl(articleValue?.url);
        if (!url) return;
        const link = element(
          'a',
          'source-link',
          articleValue?.source || articleValue?.title || 'Context source',
        );
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        links.append(link);
      });
      contextBlock.append(links);
    }
    const details = element('div', 'trend-candidate-details');
    append(
      details,
      element('p', 'field-label', 'Queries considered'),
      element('p', 'social-reason', readableList(item?.keyword_basket)),
    );
    const observations = element('ul', 'trend-observation-list');
    const observationNote = element(
      'p',
      'movement-query-caveat',
      "Feed growth is Google's recent-alert metric, not a fixed 3-month rate. Use the chart below to judge duration.",
    );
    asArray(item?.observations).forEach(observation => {
      const volume = observation?.search_volume === null || observation?.search_volume === undefined
        ? 'volume unavailable'
        : `${formatInteger(observation.search_volume)} searches`;
      const growth = observation?.growth_pct === null || observation?.growth_pct === undefined
        ? 'growth unavailable'
        : `${formatInteger(observation.growth_pct)}% feed growth`;
      const started = `first detected ${formatStarted(observation?.started_hours_ago)}`;
      observations.append(element(
        'li',
        '',
        `${observation?.geo || 'Unknown market'} · ${volume} · ${growth} · ${started}`,
      ));
    });
    append(
      body,
      heading,
      taxonomy,
      contextBlock,
      details,
      observationNote,
      observations,
      movementPanel(item),
    );
    append(article, rank, body);
    return article;
  }

  function renderTrendDiscovery(payload) {
    const discovery = payload?.trend_discovery;
    const list = $('#trend-discovery-list');
    if (!list) return;
    list.replaceChildren();
    list.setAttribute('aria-busy', 'false');
    if (!discovery || !asArray(discovery.candidates).length) {
      $('#trend-discovery-status').textContent = 'No first-layer search candidates';
      list.append(statePanel(
        'Unavailable',
        'This scan predates worldwide Google Trends discovery',
        'Run a new scan after mandatory-source preflight passes.',
        'empty-state',
      ));
      return;
    }
    const candidates = asArray(discovery.candidates).filter(item => (
      (
        state.movementGeo === 'WORLDWIDE'
        || asArray(item?.countries).includes(state.movementGeo)
      )
      && hasSelectedMovement(item)
    ));
    $('#trend-discovery-status').textContent = `${formatInteger(candidates.length)} charted search candidates · ${formatTimestamp(discovery.observed_at)}`;
    if (!candidates.length) {
      list.append(statePanel(
        'No candidates',
        'No search candidates appeared in this market',
        'Choose Worldwide or another supported country.',
        'empty-state',
      ));
      return;
    }
    candidates.forEach((item, index) => list.append(trendDiscoveryRow(item, index)));
  }

  function configureMovementControls(payload) {
    const candidates = [
      ...asArray(payload?.items),
      ...asArray(payload?.review_items),
      ...asArray(payload?.trend_discovery?.candidates),
    ];
    const bundle = candidates.find(item => item?.movement_bundle)?.movement_bundle;
    const controls = $('#movement-controls');
    if (!bundle || !controls) {
      if (controls) controls.classList.add('hidden');
      return;
    }
    const geographies = new Set(asArray(bundle.geographies).map(value => String(value?.code || '')));
    const horizons = new Set(asArray(bundle.horizons).map(value => String(value?.code || '')));
    if (!geographies.has(state.movementGeo)) {
      state.movementGeo = String(bundle.default_geo || 'WORLDWIDE');
    }
    if (!horizons.has(state.movementHorizon)) {
      state.movementHorizon = String(bundle.default_horizon || '3m');
    }
    $('#movement-geo').value = state.movementGeo;
    $('#movement-horizon').value = state.movementHorizon;
    controls.classList.remove('hidden');
  }

  function renderPrivateRadar(payload) {
    const safe = payload && typeof payload === 'object' ? payload : {};
    state.lastPrivatePayload = safe;
    configureMovementControls(safe);
    renderTrendDiscovery(safe);
    const items = asArray(safe.items).filter(hasSelectedMovement);
    const reviewItems = asArray(safe.review_items).filter(hasSelectedMovement);
    const watchItems = reviewItems.filter(item => item?.review_status === 'search_movement_only');
    const rejectedItems = reviewItems.filter(item => item?.review_status !== 'search_movement_only');
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
      $('#social-status').textContent = reviewItems.length
        ? `Scan incomplete · ${formatInteger(watchItems.length)} subjects still worth reviewing`
        : 'Latest scan failed';
    } else if (!items.length && watchItems.length) {
      $('#social-status').textContent = `0 trade-ready · ${formatInteger(watchItems.length)} worth investigating`;
    } else if (!items.length && rejectedItems.length) {
      $('#social-status').textContent = `0 trade-ready · ${formatInteger(rejectedItems.length)} topic groups rejected`;
    } else if (dataScan?.status === 'no_qualified_leads') {
      $('#social-status').textContent = 'No qualified leads';
    } else if (dataScan) {
      $('#social-status').textContent = `${formatInteger(items.length)} trade-ready · ${formatTimestamp(dataScan.completed_at || dataScan.started_at)}`;
    } else {
      $('#social-status').textContent = 'Awaiting first private scan';
    }

    if (!items.length && !reviewItems.length) {
      const running = attempt?.status === 'running';
      list.append(statePanel(
        running ? 'Scanning' : attempt?.status === 'failed' ? 'Failed' : 'No qualified leads',
        running ? 'Owned sources are being checked' : attempt?.status === 'failed' ? 'The latest scan did not complete' : 'No supported subject reached review',
        running
          ? 'Historical windows, behavior evidence, breadth, citations, and information parity are checked before anything appears.'
          : 'Raw posts and generic trends are not used as filler. Source and coverage gaps remain visible above.',
        running ? 'loading-state' : attempt?.status === 'failed' ? 'failed-state' : 'empty-state',
      ));
      return;
    }

    if (items.length) {
      list.append(radarSectionHeading(
        'Trade-ready',
        'Qualified leads',
        'Every required behavior, novelty, breadth, market-awareness, and citation check passed.',
      ));
      items.forEach((item, index) => list.append(privateRadarRow(item, index)));
    }
    if (watchItems.length) {
      list.append(radarSectionHeading(
        'Movement-backed watchlist',
        'Worth investigating',
        'Search movement is visible, but the social evidence is not yet strong enough for a trade-ready lead.',
      ));
      watchItems.forEach((item, index) => list.append(reviewRadarRow(item, index)));
    }
    if (rejectedItems.length) {
      list.append(radarSectionHeading(
        'Audit trail',
        'Rejected after review',
        'These topic groups failed evidence quality, novelty, or market-awareness checks. Reporting, dead-end anecdotes, and low-engagement posts are retained here as an audit trail, not promoted.',
      ));
      rejectedItems.forEach((item, index) => list.append(reviewRadarRow(item, index)));
    }
  }

  async function loadPrivateSnapshot() {
    const response = await fetch(PRIVATE_SNAPSHOT_URL, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Snapshot request failed (${response.status})`);
    return response.json();
  }

  async function loadPrivateRadar() {
    const list = $('#social-list');
    list.setAttribute('aria-busy', 'true');
    try {
      const payload = READ_ONLY_SNAPSHOT
        ? await loadPrivateSnapshot()
        : await api(PRIVATE_RADAR_URL);
      renderPrivateRadar(payload);
      if (!READ_ONLY_SNAPSHOT && payload?.last_attempt?.status === 'running') {
        pollPrivateScan(payload.last_attempt.id);
      }
      if (READ_ONLY_SNAPSHOT) {
        const button = $('#reload-radar');
        button.disabled = true;
        button.textContent = 'Read-only snapshot';
      }
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
    if (READ_ONLY_SNAPSHOT) {
      button.disabled = true;
      button.textContent = 'Read-only snapshot';
      return;
    }
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

  function assumptionPayload() {
    const rationale = $('#research-assumption-note').value.trim();
    const units = {
      affected_population: 'customers or units',
      behavior_change_rate: 'share',
      incremental_revenue_per_affected: 'reporting currency',
      contribution_margin: 'share',
      offsetting_costs: 'reporting currency',
    };
    const values = {};
    $$('.assumption-row').forEach(row => {
      const name = row.dataset.assumption;
      const inputs = $$('input', row);
      const raw = inputs.map(input => input.value.trim());
      if (!raw.some(Boolean)) return;
      const divide = name === 'behavior_change_rate' || name === 'contribution_margin';
      const convert = value => {
        if (!value) return null;
        const number = Number(value);
        return Number.isFinite(number) ? divide ? number / 100 : number : null;
      };
      values[name] = {
        low: convert(raw[0]),
        base: convert(raw[1]),
        high: convert(raw[2]),
        unit: units[name] || '',
        rationale: rationale || 'User-supplied explicit assumption',
      };
    });
    return values;
  }

  function researchStatusLabel(value) {
    const labels = {
      planned: 'Ready to start',
      running: 'Research running',
      complete: 'Research finished',
      partial: 'Research finished with gaps',
      error: 'Research failed',
      cancelled: 'Research cancelled',
    };
    return labels[String(value || '')] || String(value || 'unknown').replaceAll('_', ' ');
  }

  function renderInvestmentResearchProgress(run) {
    const target = $('#investment-research-progress');
    target.replaceChildren();
    if (!run) {
      target.append(statePanel('Idle', 'No company research is running', 'Choose a Radar subject and confirm the company to begin.', 'empty-state'));
      return;
    }
    const status = String(run.status || 'planned');
    const stage = ['complete', 'partial', 'error', 'cancelled'].includes(status)
      ? researchStatusLabel(status)
      : String(run.stage || status).replaceAll('_', ' ');
    const copy = run.result?.message || (
      status === 'running'
        ? 'Free company sources are being checked. Progress survives navigation.'
        : status === 'complete' || status === 'partial'
          ? 'The dossier was saved and can be reopened below.'
          : status === 'error'
            ? 'The run ended without a dossier. Source failure was not converted into an empty result.'
            : 'The run is ready to start.'
    );
    const panel = statePanel(
      status === 'running' ? `${formatInteger(run.progress)}% · Research running` : researchStatusLabel(status),
      stage,
      copy,
      status === 'error' ? 'failed-state' : status === 'running' ? 'loading-state' : '',
    );
    if ((status === 'planned' || status === 'error') && !READ_ONLY_SNAPSHOT) {
      const resume = element('button', 'primary-action compact-action', 'Resume research');
      resume.type = 'button';
      resume.addEventListener('click', () => resumeInvestmentResearch(run.id));
      panel.append(resume);
    }
    target.append(panel);
  }

  function renderInvestmentDossierList() {
    const target = $('#investment-dossier-list');
    target.replaceChildren();
    target.setAttribute('aria-busy', 'false');
    if (!state.researchRuns.length) {
      target.append(statePanel('Empty', 'No saved company research', 'Start from a Radar subject. Dossiers remain available after reload.', 'empty-state'));
      return;
    }
    state.researchRuns.forEach(run => {
      const button = element('button', `research-dossier-row${state.selectedResearchRunId === run.id ? ' selected' : ''}`);
      button.type = 'button';
      const title = run.handoff?.decision?.label || run.target?.company_name || 'Investment dossier';
      append(
        button,
        element('strong', 'row-title', title),
        element('span', 'review-badge', researchStatusLabel(run.status)),
        element('span', 'row-copy', run.target?.company_name || 'Company not reported'),
        element('span', 'row-meta mono', formatTimestamp(run.completed_at || run.created_at)),
      );
      button.addEventListener('click', () => selectInvestmentResearchRun(run));
      target.append(button);
    });
  }

  function dossierSection(title, values) {
    const section = element('section', 'dossier-section');
    section.append(element('h3', '', title));
    asArray(values).filter(Boolean).forEach(value => section.append(value));
    return section;
  }

  function renderInvestmentDossierDetail(dossier) {
    const target = $('#investment-dossier-detail');
    target.replaceChildren();
    if (!dossier) {
      target.append(statePanel('Select a dossier', 'Research detail appears here', 'Choose saved research from the list.', 'empty-state'));
      return;
    }
    append(
      target,
      element('p', 'eyebrow', 'Saved investment research'),
      element('h2', '', dossier.title || dossier.target?.company_name || 'Investment dossier'),
      element('p', 'social-summary', dossier.bottom_line || 'No bottom line was reported.'),
      element(
        'p',
        'qualification-caveat dossier-top-caveat',
        `Candidate ${String(dossier.candidate?.qualification_status || 'unknown').replaceAll('_', ' ')} · Company direction ${String(dossier.direction?.company_direction || 'uncertain').replaceAll('_', ' ')} · Materiality ${String(dossier.materiality?.status || 'unknown').replaceAll('_', ' ')}`,
      ),
    );

    const candidate = dossier.candidate?.decision || {};
    target.append(dossierSection('Observed signal', [
      element('p', '', candidate.summary || 'No candidate summary was reported.'),
      element('p', 'qualification-caveat', `Original status: ${String(dossier.candidate?.qualification_status || 'unknown').replaceAll('_', ' ')}`),
    ]));

    const direction = dossier.direction || {};
    target.append(dossierSection('Direction and falsification', [
      element('p', 'qualification-caveat', `Company direction: ${String(direction.company_direction || 'uncertain').replaceAll('_', ' ')}`),
      element('p', '', direction.possible_mechanism || 'No company mechanism was reported.'),
      element('p', 'review-status-copy', `Counterevidence: ${direction.counterevidence || 'Not reported'}`),
      element('p', 'review-status-copy', `Invalidation: ${direction.invalidation || 'Not reported'}`),
    ]));

    const materiality = dossier.materiality || {};
    const scenario = materiality.scenario || {};
    const scenarioRows = element('dl', 'signal-metrics dossier-metrics');
    append(
      scenarioRows,
      metric('Materiality', String(materiality.status || 'unknown').replaceAll('_', ' ')),
      metric('Revenue low', scenario.revenue_low ?? 'Not calculated'),
      metric('Revenue base', scenario.revenue_base ?? 'Not calculated'),
      metric('Revenue high', scenario.revenue_high ?? 'Not calculated'),
      metric('Contribution base', scenario.contribution_base ?? 'Not calculated'),
    );
    const missing = asArray(materiality.missing_reason_codes);
    target.append(dossierSection('Materiality', [
      scenarioRows,
      element('p', 'review-status-copy', materiality.limitation || 'No materiality explanation was reported.'),
      missing.length ? element('p', 'not-reported', `Still missing: ${readableList(missing.map(value => String(value).replaceAll('_', ' ')))}`) : null,
    ]));

    const facts = asArray(dossier.reported_facts);
    const factList = element('ul', 'review-reason-list');
    facts.forEach(fact => {
      const numeric = Number(fact.value);
      const displayValue = Number.isFinite(numeric)
        ? numeric.toLocaleString(undefined, { maximumFractionDigits: 4 })
        : fact.value ?? 'not reported';
      factList.append(element(
        'li',
        '',
        `${String(fact.metric || 'Reported fact').replaceAll('_', ' ')}: ${displayValue} ${fact.currency || fact.unit || ''}`,
      ));
    });
    if (!facts.length) factList.append(element('li', 'not-reported', 'No verified numeric company fact was available.'));
    target.append(dossierSection('Company disclosure', [factList]));

    const passages = element('ul', 'social-evidence-list');
    asArray(dossier.filing_passages).forEach(value => {
      const row = element('li');
      const url = safeSourceUrl(value?.source_url);
      if (url) {
        const link = element('a', 'source-link', value?.source_label || 'Open source document');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        row.append(link);
      }
      row.append(element('span', 'evidence-text', value?.text || 'No passage text'));
      passages.append(row);
    });
    if (!passages.children.length) passages.append(element('li', 'not-reported', 'No candidate-specific filing passage was found.'));
    target.append(dossierSection('Relevant filing passages', [passages]));

    const transcriptList = element('ul', 'review-reason-list');
    asArray(dossier.transcript_research?.findings).forEach(value => transcriptList.append(element(
      'li',
      '',
      `${value?.speaker || 'Speaker unavailable'}: ${value?.text || ''}`,
    )));
    if (!transcriptList.children.length) transcriptList.append(element('li', 'not-reported', 'No transcript finding was available.'));
    target.append(dossierSection('Transcript findings', [
      transcriptList,
      element('p', 'review-status-copy', dossier.transcript_research?.critical_quote_policy || ''),
    ]));

    const parity = dossier.information_parity || {};
    const coverageLinks = element('div', 'dossier-source-links');
    asArray(parity.checks).forEach(check => {
      asArray(check?.articles).forEach(article => {
        const url = safeSourceUrl(article?.url);
        if (!url) return;
        const link = element('a', 'source-link', article?.source || article?.title || 'Public coverage');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        coverageLinks.append(link);
      });
    });
    target.append(dossierSection('Public coverage checked', [
      element('p', 'qualification-caveat', `Coverage status: ${String(parity.status || 'unknown').replaceAll('_', ' ')}`),
      element('p', 'review-status-copy', parity.conclusion || 'No coverage conclusion was reported.'),
      coverageLinks.children.length ? coverageLinks : element('p', 'not-reported', 'No matching public article was returned by the sampled checks.'),
    ]));

    const sourceList = element('div', 'dossier-source-links');
    asArray(dossier.sources).forEach(value => {
      const url = safeSourceUrl(value?.url || value?.requested_url);
      if (!url) return;
      const link = element(
        'a',
        'source-link',
        `${String(value?.source_type || 'Source').replaceAll('_', ' ')} · ${String(value?.status || 'unknown').replaceAll('_', ' ')}`,
      );
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      sourceList.append(link);
    });
    target.append(dossierSection('Sources and limits', [
      sourceList,
      ...asArray(dossier.limitations).map(value => element('p', 'not-reported', value)),
    ]));
  }

  async function selectInvestmentResearchRun(run) {
    state.selectedResearchRunId = run.id;
    renderInvestmentDossierList();
    renderInvestmentResearchProgress(run);
    const target = $('#investment-dossier-detail');
    target.replaceChildren(statePanel('Loading', 'Opening saved research', 'The persisted dossier is loading.', 'loading-state'));
    if (!run.dossier_id) {
      target.replaceChildren(statePanel(
        String(run.status || 'running').replaceAll('_', ' '),
        'The dossier is not ready yet',
        run.result?.message || 'Progress remains visible above.',
        run.status === 'error' ? 'failed-state' : 'loading-state',
      ));
      if (run.status === 'running') pollInvestmentResearch(run.id);
      return;
    }
    try {
      const payload = await api(`${INVESTMENT_DOSSIER_RUNS_URL}/${encodeURIComponent(run.id)}/dossier`);
      renderInvestmentDossierDetail(payload?.dossier || null);
    } catch (error) {
      target.replaceChildren(statePanel('Failed', 'Saved dossier unavailable', error.message, 'failed-state'));
    }
  }

  async function loadInvestmentDossierRuns() {
    state.researchLoaded = true;
    const list = $('#investment-dossier-list');
    const detail = $('#investment-dossier-detail');
    const progress = $('#investment-research-progress');
    if (READ_ONLY_SNAPSHOT) {
      $$('#investment-research-form input, #investment-research-form textarea, #investment-research-form button').forEach(control => { control.disabled = true; });
      list.setAttribute('aria-busy', 'false');
      list.replaceChildren(statePanel('Read-only', 'Company research is locked in this snapshot', 'Open the authenticated private workspace to create or revisit dossiers.', 'empty-state'));
      detail.replaceChildren(statePanel('Read-only', 'No authenticated research loaded', 'This page makes no company-research API calls in snapshot mode.', 'empty-state'));
      progress.replaceChildren(statePanel('Read-only', 'No research is running', 'The published snapshot cannot start work.', 'empty-state'));
      return;
    }
    list.setAttribute('aria-busy', 'true');
    try {
      const payload = await api(`${INVESTMENT_DOSSIER_RUNS_URL}?workspace_id=default`);
      state.researchRuns = asArray(payload?.runs);
      renderInvestmentDossierList();
      const selected = state.researchRuns.find(run => run.id === state.selectedResearchRunId) || state.researchRuns[0];
      if (selected) await selectInvestmentResearchRun(selected);
      else {
        renderInvestmentResearchProgress(null);
        renderInvestmentDossierDetail(null);
      }
    } catch (error) {
      list.setAttribute('aria-busy', 'false');
      list.replaceChildren(statePanel('Failed', 'Saved research unavailable', error.message, 'failed-state'));
      showError(`Saved investment research could not be loaded: ${error.message}`);
    }
  }

  async function pollInvestmentResearch(runId) {
    if (!runId || state.researchPollingRunId === runId) return;
    state.researchPollingRunId = runId;
    try {
      for (let attempt = 0; attempt < 300; attempt += 1) {
        const payload = await api(`${INVESTMENT_DOSSIER_RUNS_URL}/${encodeURIComponent(runId)}`);
        const run = payload?.run || {};
        const index = state.researchRuns.findIndex(value => value.id === runId);
        if (index >= 0) state.researchRuns[index] = run;
        else state.researchRuns.unshift(run);
        renderInvestmentResearchProgress(run);
        renderInvestmentDossierList();
        if (run.status !== 'running' && run.status !== 'planned') {
          await loadInvestmentDossierRuns();
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
      showError('Company research is still running. Reopen Research later to check persisted progress.');
    } catch (error) {
      showError(`Company research status failed: ${error.message}`);
    } finally {
      state.researchPollingRunId = null;
      $('#research-start').disabled = false;
    }
  }

  async function resumeInvestmentResearch(runId) {
    if (READ_ONLY_SNAPSHOT) return;
    try {
      const payload = await api(`${INVESTMENT_DOSSIER_RUNS_URL}/${encodeURIComponent(runId)}/execute`, { method: 'POST' });
      if (payload?.run) renderInvestmentResearchProgress(payload.run);
      if (payload?.started) pollInvestmentResearch(runId);
    } catch (error) {
      showError(`Company research could not resume: ${error.message}`);
    }
  }

  async function startInvestmentResearch(event) {
    event.preventDefault();
    if (READ_ONLY_SNAPSHOT) return;
    clearError();
    const sourceScanId = $('#research-source-scan').value.trim();
    const candidateId = $('#research-candidate-id').value.trim();
    const companyName = $('#research-company').value.trim();
    if (!sourceScanId || !candidateId) {
      showError('Choose a Radar subject before starting company research.');
      return;
    }
    if (!companyName) {
      showError('Confirm the company legal name first.');
      $('#research-company').focus();
      return;
    }
    const primaryUrl = $('#research-primary-url').value.trim();
    const transcriptUrl = $('#research-transcript-url').value.trim();
    const selectedItem = state.researchDraft?.item || {};
    const payload = {
      workspace_id: 'default',
      source_scan_id: sourceScanId,
      candidate_id: candidateId,
      selection_mode: selectedItem?.qualification_status === 'qualified' ? 'qualified' : 'research_only',
      company_name: companyName,
      ticker: $('#research-ticker').value.trim().toUpperCase() || null,
      exchange_code: $('#research-exchange').value.trim().toUpperCase() || 'US',
      primary_document_urls: primaryUrl ? [primaryUrl] : [],
      transcript_url: transcriptUrl || null,
      assumptions: assumptionPayload(),
      idempotency_key: globalThis.crypto?.randomUUID?.() || `${candidateId}-${Date.now()}`,
    };
    const button = $('#research-start');
    button.disabled = true;
    try {
      const response = await api(INVESTMENT_DOSSIER_RUNS_URL, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const run = response?.run;
      if (!run) throw new Error('Research run was not returned');
      state.selectedResearchRunId = run.id;
      state.researchRuns = [run, ...state.researchRuns.filter(value => value.id !== run.id)];
      renderInvestmentResearchProgress(run);
      renderInvestmentDossierList();
      toast(response.started ? 'Company research started' : 'Existing company research reopened');
      if (run.status === 'running') pollInvestmentResearch(run.id);
      else await selectInvestmentResearchRun(run);
    } catch (error) {
      button.disabled = false;
      showError(`Company research could not start: ${error.message}`);
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

    $('#movement-geo').addEventListener('change', event => {
      state.movementGeo = event.target.value;
      if (state.lastPrivatePayload) renderPrivateRadar(state.lastPrivatePayload);
    });

    $('#movement-horizon').addEventListener('change', event => {
      state.movementHorizon = event.target.value;
      if (state.lastPrivatePayload) renderPrivateRadar(state.lastPrivatePayload);
    });

    $('#reload-radar').addEventListener('click', startPrivateScan);
    $('#investment-research-form').addEventListener('submit', startInvestmentResearch);

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
