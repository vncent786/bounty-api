(() => {
  'use strict';

  const TOKEN_KEY = ['bounty', 'apiToken'].join('.');
  const TRACKER_URL = '/dashboard/api/investing/tracker';
  const DECISION_STATES = new Set(['INVESTIGATING', 'PURSUE']);
  const FILTERS = [
    ['DECISION', 'Decision queue'], ['ACTIVE_TREND', 'Active trends'],
    ['WATCH', 'Watch'], ['TREND_NOTE', 'Trend notes'], ['STANDING_MONITOR', 'Standing'],
    ['REJECTED', 'Rejected'], ['ARCHIVED', 'Archived'], ['ALL', 'All'],
  ];
  const tracker = { payload: null, filter: 'DECISION', query: '', loading: false, horizons: {}, queries: {} };
  const $ = selector => document.querySelector(selector);
  const list = value => Array.isArray(value) ? value.filter(Boolean) : value ? [value] : [];

  function node(tag, className, text) {
    const value = document.createElement(tag);
    if (className) value.className = className;
    if (text !== undefined && text !== null) value.textContent = String(text);
    return value;
  }

  function add(parent, ...children) {
    children.flat().filter(Boolean).forEach(child => parent.append(child));
    return parent;
  }

  function integer(value) {
    const number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat().format(number) : '0';
  }

  function timestamp(value) {
    const parsed = value ? new Date(value) : null;
    if (!parsed || Number.isNaN(parsed.getTime())) return 'Not reported';
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
  }

  function valueText(value) {
    if (Array.isArray(value)) return value.map(valueText).filter(Boolean).join(' · ');
    if (value && typeof value === 'object') return Object.values(value).map(valueText).filter(Boolean).join(' · ');
    return String(value || '').trim();
  }

  function stateLabel(value) {
    return {
      INVESTIGATING: 'Investigating', PURSUE: 'Pursue', WATCH: 'Watch',
      TREND_NOTE: 'Trend note', STANDING_MONITOR: 'Standing monitor',
      REJECTED: 'Rejected', ARCHIVED: 'Archived',
    }[String(value || '').toUpperCase()] || 'Unknown';
  }

  function stateClass(value) {
    return `state-${String(value || 'unknown').toLowerCase().replaceAll('_', '-')}`;
  }

  function filterCount(payload, filter) {
    const ideas = list(payload?.ideas);
    if (filter === 'ALL') return ideas.length;
    if (filter === 'DECISION') return ideas.filter(item => DECISION_STATES.has(String(item?.primary_state || '').toUpperCase()) || item?.active_trend === true).length;
    if (filter === 'ACTIVE_TREND') return ideas.filter(item => item?.active_trend === true).length;
    return ideas.filter(item => String(item?.primary_state || '').toUpperCase() === filter).length;
  }

  function filteredIdeas(payload) {
    const query = tracker.query.trim().toLowerCase();
    return list(payload?.ideas).filter(item => {
      const primary = String(item?.primary_state || '').toUpperCase();
      const stateMatch = tracker.filter === 'ALL'
        || (tracker.filter === 'DECISION' && (DECISION_STATES.has(primary) || item?.active_trend === true))
        || (tracker.filter === 'ACTIVE_TREND' && item?.active_trend === true)
        || primary === tracker.filter;
      if (!stateMatch) return false;
      if (!query) return true;
      const haystack = [item?.title, item?.why, item?.detail, ...list(item?.instruments), ...list(item?.signals).map(signal => signal?.query)]
        .filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }

  function renderTabs(payload) {
    const target = $('#tracker-state-tabs');
    if (!target) return;
    target.replaceChildren();
    FILTERS.forEach(([filter, label]) => {
      const button = node('button', `tracker-tab${tracker.filter === filter ? ' active' : ''}`);
      button.type = 'button';
      button.setAttribute('aria-pressed', tracker.filter === filter ? 'true' : 'false');
      add(button, node('span', '', label), node('strong', 'mono', integer(filterCount(payload, filter))));
      button.addEventListener('click', () => {
        tracker.filter = filter;
        render(payload);
      });
      target.append(button);
    });
  }

  function signalBlock(signals) {
    const rows = list(signals);
    if (!rows.length) return null;
    const block = node('div', 'tracker-signals');
    block.append(node('span', 'tracker-field-label', 'Observed signal'));
    rows.forEach(signal => {
      const text = [signal?.query, valueText(signal?.rising), valueText(signal?.geography)].filter(Boolean).join(' · ');
      if (signal?.url) {
        const anchor = node('a', '', text || 'Open source signal');
        anchor.href = signal.url;
        anchor.target = '_blank';
        anchor.rel = 'noreferrer';
        block.append(anchor);
      } else block.append(node('span', '', text || 'Signal details not reported'));
    });
    return block;
  }

  function monitorBlock(item) {
    const monitoring = item?.monitoring && typeof item.monitoring === 'object' ? item.monitoring : { status: 'unscheduled', jobs: [] };
    const status = String(monitoring.status || 'unscheduled').toLowerCase();
    const block = node('div', 'tracker-monitor');
    add(block, node('span', 'tracker-field-label', 'Monitoring'), node('strong', `monitor-status ${status}`, status.replaceAll('_', ' ')));
    const jobs = list(monitoring.jobs);
    if (monitoring.last_result) {
      const checked = monitoring.last_checked_at ? ` on ${timestamp(monitoring.last_checked_at)}` : '';
      block.append(node('p', 'tracker-small-copy', `Last check${checked}: ${String(monitoring.last_result).replaceAll('_', ' ').toLowerCase()}.`));
    }
    if (jobs.length) {
      block.append(node('p', 'tracker-small-copy', jobs.map(job => {
        const timing = job.next_run_at ? `next ${timestamp(job.next_run_at)}` : job.schedule || 'schedule unavailable';
        return `${job.name}: ${job.state || 'unknown'}, ${timing}`;
      }).join(' · ')));
    } else if (item?.primary_state === 'WATCH') {
      block.append(node('p', 'tracker-small-copy', 'No scheduler attached yet; the finite next check remains visible.'));
    }
    return block;
  }

  function axisDate(value) {
    const parsed = value ? new Date(`${String(value).slice(0, 10)}T00:00:00Z`) : null;
    if (!parsed || Number.isNaN(parsed.getTime())) return String(value || '');
    return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: '2-digit', timeZone: 'UTC' }).format(parsed);
  }

  function signalStateLabel(value) {
    return {
      ACTIVE_TREND: 'Search rising · investment not qualified',
      DECAYING_NO_NEW_ENTRY: 'Decaying · no new entry',
      COLLAPSED_NO_NEW_ENTRY: 'Collapsed · no new entry',
      UNVERIFIED: 'Persistence unverified',
    }[String(value || '').toUpperCase()] || 'Persistence unverified';
  }

  function trendOption(bundle, query) {
    const options = list(bundle?.query_options);
    return options.find(option => String(option?.query || '') === String(query || '')) || options[0] || null;
  }

  function trendSeries(bundle, query, horizon) {
    const option = trendOption(bundle, query);
    const geography = String(bundle?.default_geo || 'WORLDWIDE');
    return option?.series?.[geography]?.[horizon] || null;
  }

  function trendClassification(bundle, query, horizon) {
    const option = trendOption(bundle, query);
    const geography = String(bundle?.default_geo || 'WORLDWIDE');
    return option?.weekly_classification?.[`${geography}:${horizon}`] || {};
  }

  function trendDataTable(series) {
    const details = node('details', 'tracker-trend-data');
    details.append(node('summary', '', 'View weekly date and value table'));
    const scroller = node('div', 'tracker-trend-table-scroll');
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const header = document.createElement('tr');
    ['Week starting', 'Week ending', 'Interest', 'Source points'].forEach(label => header.append(node('th', '', label)));
    head.append(header);
    const body = document.createElement('tbody');
    list(series?.points).forEach(point => {
      const row = document.createElement('tr');
      [axisDate(point?.date), axisDate(point?.week_end || point?.date), String(point?.value ?? ''), String(point?.source_point_count ?? '')]
        .forEach(value => row.append(node('td', '', value)));
      body.append(row);
    });
    table.append(head, body);
    scroller.append(table);
    details.append(scroller);
    return details;
  }

  function trendSvg(points, label) {
    const namespace = 'http://www.w3.org/2000/svg';
    const width = 720;
    const height = 215;
    const left = 44;
    const right = 704;
    const top = 14;
    const bottom = 166;
    const svg = document.createElementNS(namespace, 'svg');
    svg.setAttribute('class', 'tracker-trend-chart');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', label);
    [0, 50, 100].forEach(value => {
      const y = bottom - (value / 100) * (bottom - top);
      const guide = document.createElementNS(namespace, 'line');
      guide.setAttribute('class', 'tracker-trend-guide');
      guide.setAttribute('x1', String(left));
      guide.setAttribute('x2', String(right));
      guide.setAttribute('y1', String(y));
      guide.setAttribute('y2', String(y));
      const text = document.createElementNS(namespace, 'text');
      text.setAttribute('class', 'tracker-trend-axis-label');
      text.setAttribute('x', '2');
      text.setAttribute('y', String(y + 4));
      text.textContent = String(value);
      svg.append(guide, text);
    });
    const dates = points.map(point => Date.parse(`${String(point.date).slice(0, 10)}T00:00:00Z`));
    const start = Math.min(...dates);
    const end = Math.max(...dates);
    const span = Math.max(1, end - start);
    const coordinates = points.map((point, index) => {
      const stamp = dates[index];
      const x = left + ((stamp - start) / span) * (right - left);
      const value = Math.max(0, Math.min(100, Number(point.value)));
      const y = bottom - (value / 100) * (bottom - top);
      return { x, y, point };
    });
    const line = document.createElementNS(namespace, 'polyline');
    line.setAttribute('class', 'tracker-trend-line');
    line.setAttribute('fill', 'none');
    line.setAttribute('points', coordinates.map(value => `${value.x.toFixed(2)},${value.y.toFixed(2)}`).join(' '));
    svg.append(line);
    const tickIndexes = [...new Set([0, Math.round((points.length - 1) / 3), Math.round(2 * (points.length - 1) / 3), points.length - 1])];
    tickIndexes.forEach((index, tickPosition) => {
      const value = coordinates[index];
      const tick = document.createElementNS(namespace, 'line');
      tick.setAttribute('class', 'tracker-trend-tick');
      tick.setAttribute('x1', String(value.x));
      tick.setAttribute('x2', String(value.x));
      tick.setAttribute('y1', String(bottom));
      tick.setAttribute('y2', String(bottom + 5));
      const text = document.createElementNS(namespace, 'text');
      text.setAttribute('class', 'tracker-trend-date-label');
      text.setAttribute('x', String(value.x));
      text.setAttribute('y', '194');
      text.setAttribute('text-anchor', tickPosition === 0 ? 'start' : tickPosition === tickIndexes.length - 1 ? 'end' : 'middle');
      text.textContent = axisDate(value.point.date);
      svg.append(tick, text);
    });
    return svg;
  }

  function trendPanel(item) {
    const bundle = item?.search_trends && typeof item.search_trends === 'object' ? item.search_trends : null;
    if (!bundle) {
      if (item?.primary_state !== 'WATCH') return null;
      const missing = node('section', 'tracker-trend-panel tracker-trend-missing');
      add(missing, node('span', 'tracker-field-label', 'Google search history'), node('strong', '', 'Not collected'), node('p', '', 'This idea stays outside the decision queue until dated history is available.'));
      return missing;
    }
    const ideaId = String(item?.idea_id || item?.title || 'idea');
    const options = list(bundle.query_options);
    const selectedQuery = tracker.queries[ideaId] || String(bundle.default_query || bundle.query || options[0]?.query || '');
    const selectedHorizon = tracker.horizons[ideaId] || String(bundle.default_horizon || '3m');
    const series = trendSeries(bundle, selectedQuery, selectedHorizon);
    const classification = trendClassification(bundle, selectedQuery, selectedHorizon);
    const panel = node('section', 'tracker-trend-panel');
    const head = node('div', 'tracker-trend-head');
    const copy = node('div');
    add(copy, node('span', 'tracker-field-label', 'Weekly Google search interest'), node('h4', '', selectedQuery));
    const stateBadge = node('span', `tracker-signal-state ${String(classification.state || 'UNVERIFIED').toLowerCase().replaceAll('_', '-')}`, signalStateLabel(classification.state));
    add(head, copy, stateBadge);
    panel.append(head);
    const controls = node('div', 'tracker-trend-controls');
    if (options.length > 1) {
      const label = node('label', 'tracker-trend-query', 'Search');
      const select = document.createElement('select');
      select.setAttribute('aria-label', `Google search for ${item?.title || 'this idea'}`);
      options.forEach(option => {
        const choice = document.createElement('option');
        choice.value = String(option.query);
        choice.textContent = String(option.query);
        choice.selected = choice.value === selectedQuery;
        select.append(choice);
      });
      select.addEventListener('change', event => {
        tracker.queries[ideaId] = event.target.value;
        render(tracker.payload);
      });
      label.append(select);
      controls.append(label);
    }
    const horizonGroup = node('div', 'tracker-trend-horizons');
    horizonGroup.setAttribute('role', 'group');
    horizonGroup.setAttribute('aria-label', `Google history timeframe for ${item?.title || 'this idea'}`);
    [['3m', '3M'], ['1y', '1Y'], ['5y', '5Y']].forEach(([code, label]) => {
      const available = trendSeries(bundle, selectedQuery, code)?.status === 'complete';
      const button = node('button', `tracker-trend-horizon${code === selectedHorizon ? ' active' : ''}${available ? '' : ' unavailable'}`, label);
      button.type = 'button';
      button.disabled = !available;
      button.setAttribute('aria-pressed', code === selectedHorizon ? 'true' : 'false');
      button.title = available ? `Show ${label} history` : `${label} history unavailable`;
      button.addEventListener('click', () => {
        tracker.horizons[ideaId] = code;
        render(tracker.payload);
      });
      horizonGroup.append(button);
    });
    controls.append(horizonGroup);
    panel.append(controls);
    const scope = `${item?.trend_geography || bundle.default_geo || 'Worldwide'} · ${selectedHorizon.toUpperCase()} · weekly average`;
    panel.append(node('p', 'tracker-trend-scope', scope));
    const points = list(series?.points).filter(point => Number.isFinite(Number(point?.value)) && point?.date);
    if (series?.status !== 'complete' || !points.length) {
      const status = String(series?.status || 'unavailable').replaceAll('_', ' ');
      panel.append(node('div', 'tracker-trend-empty', `No chart drawn: ${status}. Missing data remain blank.`));
    } else {
      const chartScroll = node('div', 'tracker-trend-chart-scroll');
      chartScroll.append(trendSvg(points, `Weekly Google search interest for ${selectedQuery}, ${scope}`));
      panel.append(chartScroll);
      const values = points.map(point => Number(point.value));
      panel.append(node('p', 'tracker-trend-caption', `Latest ${integer(values[values.length - 1])} · peak ${integer(Math.max(...values))}. Google normalizes this chart from 0–100; these are not weekly search counts.`));
      panel.append(trendDataTable(series));
    }
    if (classification.reason) panel.append(node('p', 'tracker-trend-assessment', classification.reason));
    if (item?.theme_assessment?.reason) panel.append(node('p', 'tracker-theme-gap', `Still missing for an active investment idea: ${item.theme_assessment.reason}`));
    if (item?.economic_confirmation_required) panel.append(node('p', 'tracker-theme-gap', `Economic confirmation required: ${item.economic_confirmation_required}`));
    if (item?.geography_limit) panel.append(node('p', 'tracker-theme-gap', item.geography_limit));
    return panel;
  }

  function ideaRow(item) {
    const primary = String(item?.primary_state || 'INVESTIGATING').toUpperCase();
    const cssState = stateClass(primary);
    const article = node('article', `tracker-row ${cssState}`);
    const status = node('div', 'tracker-status-cell');
    add(status, node('span', `tracker-status-badge ${cssState}`, stateLabel(primary)), node('span', 'tracker-source-run mono', item?.source_run || 'source unavailable'));

    const thesis = node('div', 'tracker-thesis-cell');
    const heading = node('div', 'tracker-row-heading');
    heading.append(node('h3', '', item?.title || 'Untitled idea'));
    const instruments = list(item?.instruments).filter(Boolean);
    if (instruments.length) heading.append(node('span', 'tracker-instruments mono', instruments.join(' · ')));
    if (item?.transition_alert) {
      heading.append(node('span', 'tracker-transition-alert', String(item.transition_alert.state || 'Review transition').replaceAll('_', ' ')));
    }
    add(thesis, heading, node('p', 'tracker-why', item?.why || 'No decision basis reported.'));
    const signals = signalBlock(item?.signals);
    if (signals) thesis.append(signals);

    const action = node('div', 'tracker-action-cell');
    const plan = item?.transition_plan && typeof item.transition_plan === 'object' ? item.transition_plan : {};
    const nextCheck = valueText(item?.next_check || plan.next_check || item?.catalyst);
    add(
      action,
      node('span', 'tracker-field-label', primary === 'WATCH' ? 'Next decision point' : 'Next step'),
      node('p', 'tracker-next-check', nextCheck || (primary === 'REJECTED' ? 'Closed unless new evidence changes the premise.' : 'No next check scheduled.')),
      monitorBlock(item),
    );

    const trend = trendPanel(item);
    const detail = node('details', 'tracker-row-detail');
    detail.append(node('summary', '', 'Decision details'));
    const body = node('div', 'tracker-detail-body');
    if (item?.detail) add(body, node('span', 'tracker-field-label', 'What changed'), node('p', '', item.detail));
    if (plan.missing_assertion) add(body, node('span', 'tracker-field-label', 'Missing assertion'), node('p', '', valueText(plan.missing_assertion)));
    if (plan.promotion_condition) add(body, node('span', 'tracker-field-label', 'Promote if'), node('p', '', valueText(plan.promotion_condition)));
    const kill = plan.kill_condition || item?.kill_condition;
    if (kill) add(body, node('span', 'tracker-field-label', 'Kill if'), node('p', '', valueText(kill)));
    if (plan.expiry) add(body, node('span', 'tracker-field-label', 'Expiry'), node('p', '', valueText(plan.expiry)));
    add(body, node('span', 'tracker-field-label', 'Source artifact'), node('p', 'mono tracker-artifact-path', item?.source_artifact || 'Not reported'));
    detail.append(body);
    add(article, status, thesis, action, trend, detail);
    return article;
  }

  function renderDefinitions(payload) {
    const target = $('#tracker-definitions');
    if (!target) return;
    target.replaceChildren();
    Object.entries(payload?.taxonomy?.definitions || {}).forEach(([key, value]) => {
      const row = node('div', 'tracker-definition-row');
      add(row, node('strong', '', stateLabel(key)), node('p', '', value));
      target.append(row);
    });
  }

  function render(payload) {
    tracker.payload = payload;
    renderTabs(payload);
    renderDefinitions(payload);
    $('#tracker-updated').textContent = timestamp(payload?.generated_at);
    $('#tracker-trade-ready').textContent = payload?.summary?.trade_ready_now ? 'Yes' : 'No';
    $('#tracker-monitor-health').textContent = `${integer(payload?.summary?.active_monitor_jobs)} active · ${integer(payload?.summary?.paused_monitor_jobs)} paused`;

    const backlog = $('#tracker-backlog');
    backlog.replaceChildren();
    add(backlog, node('span', 'tracker-field-label', 'Research backlog'), node('strong', '', `${integer(payload?.backlog?.lineages)} ideas waiting for review`), node('p', '', payload?.backlog?.note || 'Backlog status unavailable.'));

    const target = $('#tracker-ledger');
    target.replaceChildren();
    target.setAttribute('aria-busy', 'false');
    const items = filteredIdeas(payload);
    if (!items.length) {
      const emptyCopy = tracker.filter === 'DECISION'
        ? 'No idea currently passes the persistence, broad-theme and company-economics checks. Review quarantined items under Watch.'
        : 'Change the status filter or clear the search.';
      const empty = node('div', 'state-panel empty-state');
      add(empty, node('p', 'eyebrow', 'Nothing actionable'), node('h3', '', tracker.filter === 'DECISION' ? 'Decision queue is empty' : 'Nothing matches this view'), node('p', '', emptyCopy));
      target.append(empty);
      return;
    }
    items.forEach(item => target.append(ideaRow(item)));
  }

  async function loadTracker() {
    if (tracker.loading) return;
    tracker.loading = true;
    const target = $('#tracker-ledger');
    if (target) target.setAttribute('aria-busy', 'true');
    try {
      const token = sessionStorage.getItem(TOKEN_KEY) || '';
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const response = await fetch(TRACKER_URL, { headers });
      if (!response.ok) {
        const message = response.status === 401
          ? 'Set the dashboard API token, then refresh the tracker'
          : `Tracker request failed (${response.status})`;
        throw new Error(message);
      }
      render(await response.json());
    } catch (error) {
      if (target) {
        target.replaceChildren(node('div', 'state-panel failed-state', `Investment tracker unavailable: ${error.message}`));
        target.setAttribute('aria-busy', 'false');
      }
      if ($('#tracker-updated')) $('#tracker-updated').textContent = 'Unavailable';
    } finally {
      tracker.loading = false;
    }
  }

  window.loadInvestmentTracker = loadTracker;

  document.addEventListener('DOMContentLoaded', () => {
    $('#refresh-tracker')?.addEventListener('click', loadTracker);
    document.querySelector('[data-view="monitors"]')?.addEventListener('click', loadTracker);
    $('#set-token')?.addEventListener('click', () => {
      window.setTimeout(() => {
        if (window.location.hash === '#monitors') loadTracker();
      }, 0);
    });
    $('#tracker-search')?.addEventListener('input', event => {
      tracker.query = event.target.value;
      if (tracker.payload) render(tracker.payload);
    });
    if (window.location.hash === '#monitors') loadTracker();
    window.setInterval(() => {
      if (window.location.hash === '#monitors') loadTracker();
    }, 60000);
  });
})();
