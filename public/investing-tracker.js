(() => {
  'use strict';

  const TOKEN_KEY = ['bounty', 'apiToken'].join('.');
  const TRACKER_URL = '/dashboard/api/investing/tracker';
  const ACTIVE_STATES = new Set(['INVESTIGATING', 'PURSUE', 'WATCH', 'TREND_NOTE', 'STANDING_MONITOR']);
  const FILTERS = [
    ['ACTIVE', 'Active'], ['INVESTIGATING', 'Investigating'], ['PURSUE', 'Pursue'],
    ['WATCH', 'Watch'], ['TREND_NOTE', 'Trend notes'], ['STANDING_MONITOR', 'Standing'],
    ['REJECTED', 'Rejected'], ['ARCHIVED', 'Archived'], ['ALL', 'All'],
  ];
  const tracker = { payload: null, filter: 'ACTIVE', query: '', loading: false };
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
    if (filter === 'ACTIVE') return ideas.filter(item => ACTIVE_STATES.has(String(item?.primary_state || '').toUpperCase())).length;
    return ideas.filter(item => String(item?.primary_state || '').toUpperCase() === filter).length;
  }

  function filteredIdeas(payload) {
    const query = tracker.query.trim().toLowerCase();
    return list(payload?.ideas).filter(item => {
      const primary = String(item?.primary_state || '').toUpperCase();
      const stateMatch = tracker.filter === 'ALL'
        || (tracker.filter === 'ACTIVE' && ACTIVE_STATES.has(primary))
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
    add(article, status, thesis, action, detail);
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
    add(backlog, node('span', 'tracker-field-label', 'Research backlog'), node('strong', '', `${integer(payload?.backlog?.lineages)} uninvestigated lineages`), node('p', '', payload?.backlog?.note || 'Backlog status unavailable.'));

    const target = $('#tracker-ledger');
    target.replaceChildren();
    target.setAttribute('aria-busy', 'false');
    const items = filteredIdeas(payload);
    if (!items.length) {
      add(target, node('div', 'state-panel empty-state', 'Nothing matches this status and search.'));
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
