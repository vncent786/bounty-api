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

  function safeEvidenceUrl(value) {
    try {
      const parsed = new URL(String(value || ''));
      return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
    } catch (_error) {
      return null;
    }
  }

  function evidenceLink(label, url, className = '') {
    const safe = safeEvidenceUrl(url);
    if (!safe) return null;
    const anchor = node('a', className, label);
    anchor.href = safe;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    return anchor;
  }

  function conversationVolumeNote(conversations) {
    const base = `${integer(conversations?.exact_roots)} exact posts · ${integer(conversations?.captured_comments_replies)} comments/replies observed`;
    const reviewed = conversations?.reviewed_product_relevant_comments_replies;
    if (reviewed === null || reviewed === undefined) {
      return `${base} · product-specific re-review pending against the corrected union.`;
    }
    return `${base} · ${integer(reviewed)} reviewed as product-specific.`;
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
        const jobState = String(job.state || 'unknown').toLowerCase();
        const timing = jobState === 'paused'
          ? 'no run until resumed'
          : job.next_run_at ? `next ${timestamp(job.next_run_at)}` : job.schedule || 'schedule unavailable';
        return `${job.name}: ${jobState}, ${timing}`;
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

  function availabilityHistory(availability) {
    const section = node('section', 'tracker-monitor-section tracker-availability-panel');
    add(section, node('span', 'tracker-field-label', 'Retail distribution'), node('h5', '', 'Walmart availability over time'));
    const history = list(availability?.history);
    if (!history.length) {
      section.append(node('p', 'tracker-monitor-note', 'No complete six-store history is available yet.'));
      return section;
    }
    const chart = node('div', 'tracker-availability-history');
    chart.setAttribute('role', 'img');
    chart.setAttribute('aria-label', 'Daily count of available, out-of-stock, not-listed and unverified Walmart stores');
    history.forEach(point => {
      const day = node('div', 'tracker-availability-day');
      const bar = node('div', 'tracker-availability-bar');
      [
        ['available', 'Available'],
        ['out_of_stock', 'Out of stock'],
        ['not_listed', 'Not listed'],
        ['unverified', 'Unverified'],
      ].forEach(([key, label]) => {
        const count = Number(point?.[key] || 0);
        if (!count) return;
        const segment = node('span', `tracker-availability-segment ${key}`);
        segment.style.flexGrow = String(count);
        segment.title = `${label}: ${count}`;
        segment.setAttribute('aria-label', `${label}: ${count}`);
        bar.append(segment);
      });
      add(day, bar, node('span', 'mono tracker-monitor-date', axisDate(point?.date)));
      chart.append(day);
    });
    const legend = node('div', 'tracker-monitor-legend');
    [
      ['available', 'Available'], ['out_of_stock', 'Out of stock'],
      ['not_listed', 'Not listed'], ['unverified', 'Unverified'],
    ].forEach(([key, label]) => {
      const item = node('span', '');
      add(item, node('i', `tracker-legend-key ${key}`), document.createTextNode(label));
      legend.append(item);
    });
    const current = availability?.current || availability?.last_complete || {};
    add(
      section,
      chart,
      legend,
      node('p', 'tracker-monitor-note', `Latest verified panel: ${integer(current.available)} available, ${integer(current.out_of_stock)} out of stock · ${timestamp(current.observed_at)}.`),
    );
    const latestAttempt = availability?.latest_attempt || {};
    if (latestAttempt?.coverage !== 'complete' && latestAttempt?.observed_at) {
      section.append(node(
        'p',
        'tracker-monitor-warning',
        `Latest attempt ${timestamp(latestAttempt.observed_at)}: ${integer(latestAttempt.available)} available, ${integer(latestAttempt.out_of_stock)} out of stock and ${integer(latestAttempt.unverified)} unverified. The chart keeps the last complete panel above.`,
      ));
    }
    const stores = node('div', 'tracker-store-grid');
    list(availability?.stores).forEach(store => {
      const row = node('div', `tracker-store-row ${String(store?.status || 'unverified').replaceAll('_', '-')}`);
      add(
        row,
        node('strong', '', store?.metro || 'Store'),
        node('span', 'mono', [store?.store_id, store?.postal_code].filter(Boolean).join(' · ')),
        node('em', '', store?.label || 'Unverified'),
      );
      stores.append(row);
    });
    section.append(stores);
    return section;
  }

  function dateGapDays(leftDate, rightDate) {
    const left = Date.parse(`${String(leftDate || '').slice(0, 10)}T00:00:00Z`);
    const right = Date.parse(`${String(rightDate || '').slice(0, 10)}T00:00:00Z`);
    return Number.isFinite(left) && Number.isFinite(right) ? Math.abs(right - left) / 86400000 : Infinity;
  }

  function searchGapReason(value) {
    return {
      prior_seven_day_mean_is_zero: 'the previous 7-day average was zero, so a percentage change cannot be calculated',
      partial_date_in_14_day_window: 'the 14-day comparison includes an incomplete source date',
      non_numeric_value_in_14_day_window: 'the 14-day comparison contains a missing value',
      series_missing_or_misaligned: 'the source series did not align to the dated comparison window',
    }[String(value || '')] || 'the comparison was not available';
  }

  function monitorSearchSvg(history, queries, comparisonDefinition) {
    const namespace = 'http://www.w3.org/2000/svg';
    const width = 760;
    const height = 254;
    const left = 70;
    const right = 738;
    const top = 18;
    const bottom = 194;
    const dated = history.map((point, index) => ({ point, index, time: Date.parse(`${String(point?.date || '').slice(0, 10)}T00:00:00Z`) }))
      .filter(row => Number.isFinite(row.time));
    const firstTime = dated.length ? dated[0].time : 0;
    const lastTime = dated.length ? dated[dated.length - 1].time : firstTime;
    const x = time => lastTime === firstTime ? (left + right) / 2 : left + ((time - firstTime) / (lastTime - firstTime)) * (right - left);
    const values = history.flatMap(point => queries.map(query => Number(point?.changes_pct?.[query])).filter(Number.isFinite));
    const minimum = Math.min(-25, 0, ...values);
    const maximum = Math.max(25, 0, ...values);
    const roughStep = Math.max(1, (maximum - minimum) / 4);
    const magnitude = 10 ** Math.floor(Math.log10(roughStep));
    const normalized = roughStep / magnitude;
    const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
    const floor = Math.floor(minimum / step) * step;
    const ceiling = Math.ceil(maximum / step) * step;
    const y = value => bottom - ((value - floor) / Math.max(step, ceiling - floor)) * (bottom - top);
    const frame = node('div', 'tracker-attention-chart-frame');
    const svg = document.createElementNS(namespace, 'svg');
    svg.setAttribute('class', 'tracker-attention-chart');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Rolling seven-day Google search change versus the previous seven complete days');
    const tooltip = node('output', 'tracker-chart-tooltip', 'Hover, focus, or tap a point for its exact date and comparison.');
    const definition = comparisonDefinition || 'Latest 7 complete days vs previous 7 complete days inside the same Google request.';
    const showTooltip = (query, point, value) => {
      const latest = Number(point?.latest_7_mean?.[query]);
      const prior = Number(point?.prior_7_mean?.[query]);
      const means = Number.isFinite(latest) && Number.isFinite(prior)
        ? ` Latest mean ${latest.toFixed(1)}; prior mean ${prior.toFixed(1)}.`
        : '';
      tooltip.textContent = `${axisDate(point?.date)} · ${query} · ${value >= 0 ? '+' : ''}${value.toFixed(1)}%. ${definition}${means}`;
    };

    for (let value = floor; value <= ceiling + (step / 2); value += step) {
      const guide = document.createElementNS(namespace, 'line');
      guide.setAttribute('class', `tracker-attention-guide${value === -25 ? ' cooling' : ''}`);
      guide.setAttribute('x1', String(left)); guide.setAttribute('x2', String(right));
      guide.setAttribute('y1', String(y(value))); guide.setAttribute('y2', String(y(value)));
      const label = document.createElementNS(namespace, 'text');
      label.setAttribute('class', 'tracker-trend-axis-label');
      label.setAttribute('x', '60'); label.setAttribute('y', String(y(value) + 4));
      label.setAttribute('text-anchor', 'end');
      label.textContent = `${Math.round(value)}%`;
      svg.append(guide, label);
    }
    const yTitle = document.createElementNS(namespace, 'text');
    yTitle.setAttribute('class', 'tracker-attention-y-title');
    yTitle.setAttribute('transform', 'translate(14 156) rotate(-90)');
    yTitle.textContent = 'Change vs prior 7 days (%)';
    const xTitle = document.createElementNS(namespace, 'text');
    xTitle.setAttribute('class', 'tracker-attention-x-title');
    xTitle.setAttribute('x', String((left + right) / 2)); xTitle.setAttribute('y', '246');
    xTitle.setAttribute('text-anchor', 'middle');
    xTitle.textContent = 'Through date';
    svg.append(yTitle, xTitle);

    queries.forEach((query, queryIndex) => {
      let segment = [];
      let previousDate = null;
      const flush = () => {
        if (segment.length > 1) {
          const line = document.createElementNS(namespace, 'polyline');
          line.setAttribute('class', `tracker-attention-line query-${queryIndex + 1}`);
          line.setAttribute('fill', 'none');
          line.setAttribute('points', segment.map(point => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' '));
          svg.append(line);
        }
        segment = [];
      };
      dated.forEach(({ point, time }) => {
        const value = Number(point?.changes_pct?.[query]);
        if (!Number.isFinite(value)) {
          flush();
          previousDate = null;
          const missing = point?.missing_reasons?.[query];
          if (missing) {
            const marker = document.createElementNS(namespace, 'circle');
            marker.setAttribute('class', `tracker-attention-missing query-${queryIndex + 1}`);
            marker.setAttribute('cx', String(x(time)));
            marker.setAttribute('cy', String(bottom));
            marker.setAttribute('r', '5');
            marker.setAttribute('tabindex', '0');
            marker.setAttribute('role', 'button');
            const reason = searchGapReason(missing);
            const missingLabel = `${axisDate(point?.date)} · ${query} · not comparable because ${reason}.`;
            marker.setAttribute('aria-label', missingLabel);
            const showMissing = () => { tooltip.textContent = missingLabel; };
            marker.addEventListener('pointerenter', showMissing);
            marker.addEventListener('focus', showMissing);
            marker.addEventListener('click', showMissing);
            marker.addEventListener('keydown', event => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                showMissing();
              }
            });
            svg.append(marker);
          }
          return;
        }
        if (previousDate && dateGapDays(previousDate, point?.date) > 1.5) flush();
        const coordinate = { x: x(time), y: y(value), value, point };
        segment.push(coordinate);
        previousDate = point?.date;
        const circle = document.createElementNS(namespace, 'circle');
        circle.setAttribute('class', `tracker-attention-point query-${queryIndex + 1}`);
        circle.setAttribute('cx', String(coordinate.x)); circle.setAttribute('cy', String(coordinate.y)); circle.setAttribute('r', '5');
        circle.setAttribute('tabindex', '0');
        circle.setAttribute('role', 'button');
        const pointLabel = `${query}: ${value >= 0 ? '+' : ''}${value.toFixed(1)}% through ${axisDate(point?.date)}. ${definition}`;
        circle.setAttribute('aria-label', pointLabel);
        const title = document.createElementNS(namespace, 'title');
        title.textContent = pointLabel;
        circle.append(title);
        circle.addEventListener('pointerenter', () => showTooltip(query, point, value));
        circle.addEventListener('focus', () => showTooltip(query, point, value));
        circle.addEventListener('click', () => showTooltip(query, point, value));
        circle.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            showTooltip(query, point, value);
          }
        });
        svg.append(circle);
      });
      flush();
    });
    const tickIndexes = new Set([0, Math.floor((dated.length - 1) / 3), Math.floor(((dated.length - 1) * 2) / 3), dated.length - 1]);
    dated.forEach(({ point, index, time }, datedIndex) => {
      if (!tickIndexes.has(datedIndex)) return;
      const label = document.createElementNS(namespace, 'text');
      label.setAttribute('class', 'tracker-trend-date-label');
      label.setAttribute('x', String(x(time))); label.setAttribute('y', '216');
      label.setAttribute('text-anchor', datedIndex === 0 ? 'start' : datedIndex === dated.length - 1 ? 'end' : 'middle');
      label.textContent = axisDate(point?.date);
      svg.append(label);
    });
    frame.append(svg, tooltip);
    return frame;
  }

  function searchAttentionPanel(search) {
    const section = node('section', 'tracker-monitor-section tracker-search-attention');
    add(
      section,
      node('span', 'tracker-field-label', 'Search attention change'),
      node('h5', '', 'Rolling 7-day search change'),
      node('p', 'tracker-monitor-lead', search?.current_read || 'Search direction unavailable.'),
    );
    const history = list(search?.rolling_seven_day_timeline).length
      ? list(search.rolling_seven_day_timeline)
      : list(search?.rolling_seven_day_change);
    const queries = list(search?.query_basket).length
      ? list(search.query_basket)
      : [...new Set(history.flatMap(point => Object.keys(point?.changes_pct || {})))];
    if (history.length && queries.length) {
      const scroll = node('div', 'tracker-attention-chart-scroll');
      scroll.append(monitorSearchSvg(history, queries, search?.comparison_definition));
      section.append(scroll);
      const legend = node('div', 'tracker-monitor-legend');
      queries.forEach((query, index) => {
        const item = node('span', '');
        add(item, node('i', `tracker-legend-key query-${index + 1}`), document.createTextNode(query));
        legend.append(item);
      });
      section.append(legend);
      section.append(node('p', 'tracker-monitor-note', `${search?.geography || 'US'} · Google web search · ${search?.comparison_definition || 'Latest 7 complete days vs previous 7 complete days inside the same Google request.'} Blank dates are missing, never zero or interpolated.`));
      const details = node('details', 'tracker-monitor-table');
      details.append(node('summary', '', 'View daily rolling changes'));
      const table = document.createElement('table');
      const head = document.createElement('thead');
      const header = document.createElement('tr');
      ['Through', ...queries].forEach(label => header.append(node('th', '', label)));
      head.append(header);
      const body = document.createElement('tbody');
      history.forEach(point => {
        const row = document.createElement('tr');
        row.append(node('td', '', axisDate(point?.date)));
        queries.forEach(query => {
          const value = Number(point?.changes_pct?.[query]);
          const missing = point?.missing_reasons?.[query];
          const cell = node('td', '', Number.isFinite(value)
            ? `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`
            : missing ? 'Not comparable' : 'Missing');
          if (missing) cell.title = searchGapReason(missing);
          row.append(cell);
        });
        body.append(row);
      });
      table.append(head, body);
      details.append(table);
      section.append(details);
    } else {
      section.append(node('p', 'tracker-monitor-warning', 'No complete 14-day comparison window is available. Missing dates remain blank.'));
    }
    const searchHealth = search?.source_health || {};
    if (searchHealth?.visible_series_uses_last_verified && searchHealth?.latest_attempt_observed_at) {
      section.append(node(
        'p',
        'tracker-monitor-warning',
        `Latest search attempt ${timestamp(searchHealth.latest_attempt_observed_at)} was incomplete. The chart keeps the last verified series through ${axisDate(search?.latest_complete_date)}.`,
      ));
    }
    return section;
  }

  function countHistoryBlock(history, options) {
    const rows = list(history);
    const block = node('div', `tracker-count-history ${options.className || ''}`.trim());
    block.append(node('h6', '', options.title));
    if (!rows.length) {
      block.append(node('p', 'tracker-monitor-warning', options.emptyCopy));
      return block;
    }
    const values = rows.flatMap(row => [options.primaryKey, options.secondaryKey]
      .map(key => row?.[key])
      .filter(value => value !== null && value !== undefined && Number.isFinite(Number(value)))
      .map(Number));
    const ceiling = Math.max(1, ...values);
    const chart = node('div', 'tracker-count-chart');
    chart.setAttribute('role', 'img');
    chart.setAttribute('aria-label', options.ariaLabel);
    let hasMissing = false;
    rows.forEach(reading => {
      const day = node('div', 'tracker-count-day');
      const bars = node('div', 'tracker-count-bars');
      [[options.primaryKey, 'primary'], [options.secondaryKey, 'secondary']].forEach(([key, className]) => {
        const raw = reading?.[key];
        const available = raw !== null && raw !== undefined && Number.isFinite(Number(raw));
        const bar = node('span', `tracker-count-bar ${className}${available ? '' : ' missing'}`);
        if (available) {
          const value = Number(raw);
          bar.style.height = `${Math.max(value ? 5 : 0, (value / ceiling) * 100)}%`;
          bar.title = `${options.labels[className]}: ${value}`;
          bar.setAttribute('aria-label', `${options.labels[className]}: ${value}`);
        } else {
          hasMissing = true;
          bar.textContent = '—';
          bar.title = `${options.labels[className]}: not collected`;
          bar.setAttribute('aria-label', `${options.labels[className]}: not collected`);
        }
        bars.append(bar);
      });
      add(day, bars, node('span', 'mono tracker-monitor-date', axisDate(reading?.observed_at)));
      chart.append(day);
    });
    const legend = node('div', 'tracker-monitor-legend');
    [['primary', options.labels.primary], ['secondary', options.labels.secondary]].forEach(([key, label]) => {
      const item = node('span', '');
      add(item, node('i', `tracker-legend-key count-${key}`), document.createTextNode(label));
      legend.append(item);
    });
    if (hasMissing) {
      const item = node('span', '');
      add(item, node('i', 'tracker-legend-key count-missing'), document.createTextNode('Not collected'));
      legend.append(item);
    }
    add(block, chart, legend);
    return block;
  }

  function linkedEvidenceList(rows, emptyCopy) {
    const block = node('div', 'tracker-evidence-list');
    const values = list(rows);
    if (!values.length) {
      block.append(node('p', 'tracker-monitor-note', emptyCopy));
      return block;
    }
    values.forEach((row, index) => {
      const item = node('article', 'tracker-evidence-row');
      const type = String(row?.record_type || 'post').replaceAll('_', ' ');
      const platform = String(row?.platform || 'source');
      const author = row?.author ? ` · @${row.author}` : '';
      const linked = evidenceLink(`${index + 1}. ${type} · ${platform}${author}`, row?.url, 'tracker-evidence-link');
      if (linked) item.append(linked);
      if (row?.text) item.append(node('p', '', row.text));
      const evidenceDetails = [row?.content_origin && String(row.content_origin).replaceAll('_', ' '), row?.created_at && axisDate(row.created_at)]
        .filter(Boolean).join(' · ');
      if (evidenceDetails) item.append(node('span', 'tracker-monitor-date', evidenceDetails));
      block.append(item);
    });
    return block;
  }

  function conversationEvidencePanel(evidence) {
    const details = node('details', 'tracker-conversation-evidence');
    const linkCount = integer(evidence?.total_clickable_links);
    details.append(node('summary', '', `Inspect linked conversation evidence · ${linkCount} source links`));
    const verified = evidence?.status === 'verified';
    details.append(node('p', verified ? 'tracker-evidence-verified' : 'tracker-monitor-warning', verified
      ? 'Every displayed post and comment/reply count matches the persisted linked evidence below.'
      : 'Displayed counts do not fully reconcile to linked evidence. Treat the affected platform rows as an audit gap.'));
    const platformLabels = { x: 'X', tiktok: 'TikTok', instagram: 'Instagram', reddit: 'Reddit', youtube: 'YouTube' };
    const platforms = node('div', 'tracker-evidence-platforms');
    Object.entries(evidence?.platforms || {}).forEach(([platform, row]) => {
      const section = node('details', `tracker-evidence-platform status-${row?.count_status || 'unknown'}`);
      section.append(node('summary', '', `${platformLabels[platform] || platform} · ${integer(row?.linked_original_posts)} posts · ${integer(row?.linked_comments_replies)} comments/replies${row?.count_status === 'verified' ? ' · verified' : ' · mismatch'}`));
      const columns = node('div', 'tracker-evidence-columns');
      const posts = node('section', '');
      add(posts, node('h6', '', `Original posts (${integer(row?.linked_original_posts)})`), linkedEvidenceList(row?.original_posts, 'No linked original posts.'));
      const responses = node('section', '');
      add(responses, node('h6', '', `Comments and replies (${integer(row?.linked_comments_replies)})`), linkedEvidenceList(row?.comments_replies, 'No linked comments or replies were captured.'));
      add(columns, posts, responses);
      section.append(columns);
      platforms.append(section);
    });
    details.append(platforms);
    return details;
  }

  function sentimentEvidencePanel(sentiment) {
    const rows = list(sentiment?.evidence);
    if (!rows.length) return null;
    const details = node('details', 'tracker-sentiment-evidence');
    details.append(node('summary', '', `Inspect linked sentiment classifications · ${integer(rows.length)} comments/replies`));
    const evidenceList = node('div', 'tracker-evidence-list');
    rows.forEach((row, index) => {
      const item = node('article', `tracker-evidence-row sentiment-${String(row?.label || 'unclassified')}`);
      const link = evidenceLink(`${index + 1}. ${String(row?.platform || 'source')} · ${String(row?.label || 'unclassified').replaceAll('_', ' ')}`, row?.url, 'tracker-evidence-link');
      if (link) item.append(link);
      if (row?.basis) item.append(node('p', '', row.basis));
      evidenceList.append(item);
    });
    add(details, node('p', 'tracker-monitor-note', sentiment?.coverage_note || 'This is a linked sample, not a classification of every displayed post.'), evidenceList);
    return details;
  }

  function conversationPanel(conversations) {
    const section = node('section', 'tracker-monitor-section tracker-conversation-panel');
    add(
      section,
      node('span', 'tracker-field-label', 'Observed conversation volume'),
      node('h5', '', conversations?.headline || `${integer(conversations?.exact_roots)} exact posts successfully observed`),
      node('p', 'tracker-monitor-lead', conversations?.current_read || 'Conversation direction unavailable.'),
      node('p', 'tracker-monitor-note', conversationVolumeNote(conversations)),
    );
    section.append(countHistoryBlock(conversations?.history, {
      title: 'Observed conversation volume over time',
      className: 'tracker-buzz-history',
      primaryKey: 'exact_roots',
      secondaryKey: 'captured_comments_replies',
      labels: { primary: 'Exact posts', secondary: 'Comments/replies' },
      ariaLabel: 'Exact original posts and captured comments or replies by monitoring date',
      emptyCopy: 'No dated conversation-volume history yet.',
    }));
    section.append(node('p', 'tracker-monitor-note', conversations?.coverage_note || 'Observed volume is shown from successful sources.'));
    const conversationHealth = conversations?.source_health || {};
    if (conversationHealth?.visible_read_uses_last_verified) {
      section.append(node(
        'p',
        'tracker-monitor-warning',
        `Latest attempt ${timestamp(conversationHealth.latest_attempt_observed_at)} was incomplete. The chart keeps the last fully source-linked observation from ${timestamp(conversationHealth.visible_observed_at)}.`,
      ));
    }
    const sentiment = conversations?.sentiment || {};
    const sentimentCounts = sentiment?.counts && typeof sentiment.counts === 'object' ? sentiment.counts : null;
    if (sentimentCounts) {
      const sentimentBlock = node('div', 'tracker-sentiment-block');
      sentimentBlock.append(node('span', 'tracker-field-label', 'Sentiment mix · context only'));
      const sentimentGrid = node('div', 'tracker-sentiment-grid');
      [['positive', 'Positive'], ['negative', 'Negative'], ['neutral', 'Neutral'], ['mixed', 'Mixed'], ['unclassified', 'Not enough text']]
        .forEach(([key, label]) => {
          const cell = node('div', `tracker-sentiment-cell ${key}`);
          add(cell, node('strong', 'mono', integer(sentimentCounts[key])), node('span', '', label));
          sentimentGrid.append(cell);
        });
      add(sentimentBlock, sentimentGrid, node('p', 'tracker-monitor-note', sentiment.coverage_note || sentiment.note || 'Positive and negative both count toward buzz.'));
      const sentimentEvidence = sentimentEvidencePanel(sentiment);
      if (sentimentEvidence) sentimentBlock.append(sentimentEvidence);
      section.append(sentimentBlock);
    } else {
      section.append(node('p', 'tracker-monitor-note', sentiment.status === 'not_collected'
        ? 'Sentiment mix is pending. Positive and negative both count toward buzz.'
        : sentiment.note || 'Sentiment is context only; total buzz counts reactions of either sign.'));
    }
    const grid = node('div', 'tracker-platform-grid');
    Object.entries(conversations?.platforms || {}).forEach(([platform, reading]) => {
      const health = String(reading?.health || 'unknown');
      const query = String(reading?.query_status || 'not run').toLowerCase();
      const usableBoundedSample = query === 'partial' && Number(reading?.exact_roots || 0) > 0;
      if (health !== 'healthy' || (!['complete', 'complete_relevant', 'complete_no_match', 'empty'].includes(query) && !usableBoundedSample)) return;
      const queryLabel = {
        empty: 'No current match',
        complete: 'GHOST check complete',
        complete_relevant: 'GHOST check complete',
        complete_no_match: 'No current match',
        failed: 'GHOST check incomplete',
        partial: 'Observed bounded sample',
        'not run': 'GHOST check not run',
      }[query] || query.replaceAll('_', ' ');
      const row = node('div', `tracker-platform-row health-${health}`);
      const platformLabel = {
        x: 'X', tiktok: 'TikTok', instagram: 'Instagram', reddit: 'Reddit', youtube: 'YouTube',
      }[platform] || platform;
      const responseStatus = String(reading?.response_collection_status || '');
      const responseText = responseStatus === 'not_collected'
        ? 'comments/replies not collected'
        : `${integer(reading?.captured_comments_replies)} comments/replies${responseStatus === 'bounded_with_gaps' ? ' · bounded with gaps' : ''}`;
      add(
        row,
        node('strong', '', platformLabel),
        node('span', '', 'Observed'),
        node('span', '', queryLabel),
        node('span', 'mono', `${integer(reading?.exact_roots)} posts · ${responseText}${reading?.reviewed_product_relevant_comments_replies !== null && reading?.reviewed_product_relevant_comments_replies !== undefined ? ` · ${integer(reading.reviewed_product_relevant_comments_replies)} product-specific` : ''}`),
      );
      if (reading?.note) row.title = reading.note;
      grid.append(row);
    });
    section.append(grid, conversationEvidencePanel(conversations?.evidence || {}));
    return section;
  }

  function streetCoveragePanel(coverage) {
    const section = node('section', 'tracker-monitor-section tracker-street-panel');
    const outlets = Number(coverage?.qualifying_outlets || 0);
    const management = coverage?.management_acknowledged === true;
    const summary = outlets
      ? `${integer(outlets)} qualifying business or financial outlets cover the exact KDP implication.`
      : 'No qualifying business or financial outlet covers the exact KDP implication yet.';
    add(
      section,
      node('span', 'tracker-field-label', 'Information parity'),
      node('h5', '', 'Street awareness'),
      node('p', 'tracker-monitor-lead', summary),
      node('p', 'tracker-monitor-note', management ? 'KDP management has explicitly acknowledged A&W economics.' : 'KDP management has not attributed sales, volume, margin or guidance to A&W.'),
      node('p', 'tracker-monitor-date', `Checked ${timestamp(coverage?.observed_at)}`),
    );
    const sourceHealth = coverage?.source_health || {};
    if (sourceHealth?.visible_read_uses_last_verified) {
      const gaps = list(sourceHealth?.source_gaps).map(value => String(value).replaceAll('_', ' '));
      section.append(node(
        'p',
        'tracker-monitor-warning',
        `Latest public-source check at ${timestamp(sourceHealth?.latest_attempt_observed_at)} was incomplete${gaps.length ? ` (${gaps.join(', ')})` : ''}. The figures above retain the last verified check; missing coverage is not counted as silence.`,
      ));
    }
    section.append(countHistoryBlock(coverage?.history, {
      title: 'News and management coverage over time',
      className: 'tracker-news-history',
      primaryKey: 'qualifying_outlets',
      secondaryKey: 'management_acknowledged',
      labels: { primary: 'Qualifying outlets', secondary: 'Management acknowledgment' },
      ariaLabel: 'Qualifying financial outlets and management acknowledgment by monitoring date',
      emptyCopy: 'No dated financial-news coverage history yet.',
    }));
    const laneLabels = {
      official_ir: 'Official IR',
      regulator_filings: 'SEC filings',
      earnings_calls: 'Earnings calls',
      official_product_context: 'Official product context',
      qualifying_business_news: 'Business news',
      sell_side_public_mentions: 'Public sell-side mentions',
    };
    const lanes = node('div', 'tracker-parity-lanes');
    const laneEntries = Object.entries(coverage?.lanes || {});
    const completedLanes = laneEntries.filter(([, lane]) => lane?.status === 'complete').length;
    section.append(node('p', 'tracker-monitor-note', `Daily public-source run · ${integer(completedLanes)} of ${integer(laneEntries.length)} lanes completed for this check.`));
    laneEntries.forEach(([key, lane]) => {
      const card = node('article', `tracker-parity-lane status-${String(lane?.status || 'unknown').replaceAll('_', '-')}`);
      const checked = integer(lane?.checked_count);
      const retrieved = integer(lane?.retrieved_count);
      const qualifying = integer(lane?.qualifying_count);
      add(card,
        node('strong', '', laneLabels[key] || key.replaceAll('_', ' ')),
        node('span', 'tracker-parity-status', lane?.status === 'complete' ? (key === 'earnings_calls' ? 'Earnings calls checked' : 'Checked') : String(lane?.status || 'Not checked').replaceAll('_', ' ')),
        node('p', '', `${checked} checked · ${retrieved} directly read · ${qualifying} exact economic matches`));
      list(lane?.events).forEach(event => {
        const link = evidenceLink(`${event?.event_name || 'Earnings event'} · ${event?.event_date || 'date unavailable'}`, event?.url, 'tracker-parity-link');
        if (link) card.append(link);
      });
      const evidence = list(lane?.evidence);
      if (evidence.length) {
        const detail = node('details', 'tracker-parity-evidence');
        detail.append(node('summary', '', `Open ${integer(evidence.length)} source${evidence.length === 1 ? '' : 's'}`));
        evidence.forEach((row, index) => {
          const attributes = row?.attributes || {};
          const label = row?.title || attributes?.outlet || `${laneLabels[key] || key} source ${index + 1}`;
          const link = evidenceLink(label, row?.url, 'tracker-parity-link');
          if (link) detail.append(link);
        });
        card.append(detail);
      }
      lanes.append(card);
    });
    section.append(lanes);
    const paywalled = coverage?.paywalled_research || {};
    section.append(node('p', 'tracker-monitor-note', paywalled.note || 'Paywalled or private research is outside this public monitor and is not represented as checked.'));
    return section;
  }

  const EXIT_STATE_LABELS = {
    NO_EXIT_TRIGGER_VERIFIED: 'No exit trigger verified',
    HUMAN_EXIT_REVIEW_REQUIRED: 'Human exit review required',
    THESIS_INVALIDATION_REVIEW: 'Thesis invalidation review',
    INFORMATION_PARITY_REVIEW: 'Information parity review',
    EXPIRY_REVIEW: 'Expiry review',
    DATA_INCOMPLETE: 'Data incomplete — no exit call',
  };

  function exitStateLabel(value) {
    return EXIT_STATE_LABELS[String(value || '').toUpperCase()] || String(value || 'Unknown').replaceAll('_', ' ');
  }

  function money(value, blank) {
    const number = Number(value);
    return Number.isFinite(number)
      ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(number)
      : blank;
  }

  function blank() {
    return node('span', 'tracker-exit-blank', '—');
  }

  function exitDecisionBanner(exit) {
    const banner = exit?.decision_banner || {};
    const state = String(banner.review_state || 'DATA_INCOMPLETE').toUpperCase();
    const section = node('section', `tracker-exit-banner state-${state.toLowerCase().replaceAll('_', '-')}`);
    const head = node('div', 'tracker-exit-banner-head');
    const copy = node('div');
    add(copy, node('span', 'tracker-field-label', 'Exit decision'), node('h4', '', exitStateLabel(state)));
    const badges = node('div', 'tracker-exit-badges');
    add(badges,
      node('strong', `tracker-exit-state-badge ${state.toLowerCase().replaceAll('_', '-')}`, exitStateLabel(state)),
      node('span', 'tracker-monitor-asof mono', `As of ${timestamp(banner.as_of)}`));
    add(head, copy, badges);
    add(section, head, node('p', 'tracker-exit-reason', banner.plain_english_reason || 'Decision reason unavailable.'));
    const met = list(banner.exit_review_triggers_met);
    const notMet = list(banner.exit_review_triggers_not_met);
    if (met.length) {
      const metBlock = node('div', 'tracker-exit-met');
      add(metBlock, node('span', 'tracker-field-label', 'Exit-review triggers met'));
      met.forEach(row => add(metBlock, node('p', '', `${String(row?.trigger || '').replaceAll('_', ' ')} — ${row?.evidence || 'no evidence recorded'} (${timestamp(row?.observed_at)})`)));
      section.append(metBlock);
    }
    if (notMet.length) {
      const notMetBlock = node('details', 'tracker-exit-notmet');
      notMetBlock.append(node('summary', '', `Exit-review triggers not met (${notMet.length})`));
      notMet.forEach(row => notMetBlock.append(node('p', '', `${String(row?.trigger || '').replaceAll('_', ' ')} — ${row?.evidence || 'no evidence recorded'}${row?.next_check ? ` · next check: ${row.next_check}` : ''}`)));
      section.append(notMetBlock);
    }
    const gaps = list(banner.critical_data_gaps);
    if (gaps.length) {
      const gapBlock = node('div', 'tracker-exit-gaps');
      add(gapBlock, node('span', 'tracker-field-label', 'Critical data gaps'));
      gaps.forEach(row => add(gapBlock, node('p', '', `${String(row?.gap || '').replaceAll('_', ' ')}: ${row?.detail || ''}`)));
      section.append(gapBlock);
    }
    add(section, node('p', 'tracker-exit-human-only', 'Human review only. No button, job or alert on this dashboard can place or close a trade.'));
    return section;
  }

  function exitPositionClock(exit) {
    const position = exit?.position_and_clock || {};
    const section = node('section', 'tracker-exit-position');
    add(section, node('span', 'tracker-field-label', 'Private position and expiry clock'));
    const clock = node('div', 'tracker-exit-clock');
    const days = Number(position.days_to_expiry);
    const clockCells = [
      ['Instrument', `${position.underlying || '?'} ${money(position.strike_usd, position.strike_usd)} ${String(position.option_right || 'call')} · ${Number(position.contracts) || 0} contracts (${Number(position.underlying_units) || 0} units)`],
      ['Expiry', `${position.expiry || '?'} · ${Number.isFinite(days) ? `${days} calendar days left (as of ${position.days_to_expiry_as_of || '?'})` : 'days remaining unknown'}`],
      ['Entry / cost', `Entered ${position.entry_date || '?'} · $${Number(position.premium_per_underlying_unit_usd) || 0} per unit · ${money(position.premium_paid_usd, '—')} premium paid`],
      ['At-expiry breakeven', `${money(position.at_expiry_premium_breakeven_usd, '—')} (${position.at_expiry_premium_breakeven_formula || 'strike + premium'})`],
      ['Contractual max loss', `${money(position.contractual_max_loss_usd, '—')} (long-call premium; contractual, not a user choice)`],
      ['User loss cap', position.user_maximum_acceptable_loss_usd === null || position.user_maximum_acceptable_loss_usd === undefined
        ? 'Not set by user — price-loss alert disabled'
        : `${money(position.user_maximum_acceptable_loss_usd, '—')} (alert ${position.loss_alert_enabled ? 'enabled' : 'disabled'})`],
      ['Next catalyst', `${position.next_catalyst || 'None scheduled'}${position.next_catalyst_scheduled_at ? ` · ${timestamp(position.next_catalyst_scheduled_at)}` : ''}`],
      ['Intended horizon', position.intended_horizon || 'Not recorded'],
    ];
    clockCells.forEach(([label, value]) => {
      const cell = node('div', 'tracker-exit-clock-cell');
      add(cell, node('span', 'tracker-field-label', label), node('p', '', value));
      clock.append(cell);
    });
    section.append(clock);
    return section;
  }

  function exitRiskBoundaries(exit) {
    const risk = exit?.risk_boundaries || {};
    const section = node('section', 'tracker-exit-risk');
    add(section, node('span', 'tracker-field-label', 'User risk and time-decay boundaries'));
    const grid = node('div', 'tracker-exit-clock');
    const loss = risk.maximum_acceptable_loss;
    const cells = [
      ['Maximum acceptable loss', typeof loss === 'number'
        ? `${money(loss)} — price-loss alert ${risk.loss_alert_enabled ? 'enabled' : 'disabled'}`
        : `${loss || 'Not set by user'} — price-loss alert ${risk.loss_alert_enabled ? 'enabled' : 'disabled'}`],
      ['Pre-expiry close or roll policy', risk.pre_expiry_close_or_roll_policy || 'Not set by user'],
      ['Time-decay review threshold', risk.time_decay_review_threshold || 'Not set by user'],
    ];
    cells.forEach(([label, value]) => {
      const cell = node('div', 'tracker-exit-clock-cell');
      add(cell, node('span', 'tracker-field-label', label), node('p', '', String(value)));
      grid.append(cell);
    });
    add(section, grid, node('p', 'tracker-monitor-note', risk.rule || 'Missing boundaries stay Not set by user and disable only their own alert.'));
    return section;
  }

  function exitTriggerMatrix(exit) {
    const banner = exit?.decision_banner || {};
    const rows = [...list(banner.exit_review_triggers_met), ...list(banner.exit_review_triggers_not_met)];
    const section = node('section', 'tracker-exit-triggers');
    add(section, node('span', 'tracker-field-label', 'Exit-review trigger matrix'), node('p', 'tracker-monitor-note', 'Each trigger is met, not met, unresolved or not set, with its evidence and timestamp. A met trigger requests human review; it never executes a trade.'));
    const scroll = node('div', 'tracker-exit-table-scroll');
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const header = document.createElement('tr');
    ['Trigger', 'Status', 'Evidence', 'Observed', 'Next check'].forEach(label => header.append(node('th', '', label)));
    head.append(header);
    const body = document.createElement('tbody');
    rows.forEach(row => {
      const tr = document.createElement('tr');
      const status = String(row?.status || 'met').toLowerCase().replaceAll('_', ' ');
      add(tr,
        node('td', '', String(row?.trigger || '').replaceAll('_', ' ')),
        node('td', `tracker-exit-status ${status.replaceAll(' ', '-')}`, status),
        node('td', '', row?.evidence || '—'),
        node('td', '', timestamp(row?.observed_at)),
        node('td', '', row?.next_check || '—'));
      body.append(tr);
    });
    table.append(head, body);
    scroll.append(table);
    section.append(scroll);
    return section;
  }

  function exitThesisChain(exit) {
    const chain = exit?.thesis_chain || {};
    const section = node('section', 'tracker-exit-thesis');
    add(section, node('span', 'tracker-field-label', 'Thesis chain'), node('p', 'tracker-exit-focal', chain.focal_proposition || 'Focal proposition unavailable.'));
    const grid = node('div', 'tracker-exit-thesis-grid');
    list(chain.checks).forEach(check => {
      const state = String(check?.current_state || 'not_evaluated');
      const card = node('article', `tracker-exit-thesis-card state-${state.toLowerCase().replaceAll('_', '-')}`);
      add(card,
        node('h5', '', check?.label || check?.check || 'Check'),
        node('span', 'tracker-exit-status ' + state.toLowerCase().replaceAll('_', '-'), state.replaceAll('_', ' ')),
        node('p', '', check?.current_evidence || 'No evidence recorded.'),
        node('p', 'tracker-monitor-date mono', `Observed ${timestamp(check?.observed_at)} · ${check?.source || 'source not recorded'}`));
      grid.append(card);
    });
    add(section, grid, node('p', 'tracker-monitor-note', chain.display_rule || 'The four checks are never collapsed into one score.'));
    const invalidation = exit?.invalidation_review || {};
    const invBlock = node('details', 'tracker-exit-invalidation');
    invBlock.append(node('summary', '', `Thesis invalidation review · ${list(invalidation.triggers).length} persisted tests · ${integer(invalidation.verified_count)} verified`));
    list(invalidation.triggers).forEach((row, index) => invBlock.append(node('p', '', `${index + 1}. ${row}`)));
    invBlock.append(node('p', 'tracker-monitor-note', invalidation.automation_rule || 'A verified trigger requests human review. It never executes or mandates a sale.'));
    section.append(invBlock);
    return section;
  }

  function exitParity(exit) {
    const parity = exit?.information_parity || {};
    const section = node('section', 'tracker-exit-parity');
    add(section,
      node('span', 'tracker-field-label', 'Exact information-parity review trigger'),
      node('p', 'tracker-exit-implication', parity.exact_implication || 'Exact implication unavailable.'),
      node('p', 'tracker-monitor-lead', `Current state: ${parity.current_state || 'unknown'} · ${integer(parity.qualifying_outlets)} qualifying outlets · management acknowledgment ${parity.management_acknowledgment ? 'present' : 'absent'} · checked ${timestamp(parity.observed_at)}.`),
      node('p', 'tracker-monitor-note', `Human review trigger: ${parity.human_review_trigger || 'not recorded'}`));
    const rules = node('ul', 'tracker-exit-rules');
    list(parity.rules).forEach(rule => rules.append(node('li', '', rule)));
    section.append(rules);
    return section;
  }

  function exitMarketContext(exit) {
    const context = exit?.market_and_option_context || {};
    const current = context.current || {};
    const snapshot = context.last_verified_snapshot;
    const section = node('section', 'tracker-exit-market');
    add(section, node('span', 'tracker-field-label', 'Market and option context'));
    const rows = [
      ['Underlying bid / ask', [current.underlying_bid, current.underlying_ask]],
      ['Option bid / ask / last', [current.option_bid, current.option_ask, current.option_last]],
      ['Option market value / unrealized P&L', [current.option_market_value, current.unrealized_pnl]],
      ['Intrinsic / extrinsic value', [current.intrinsic_value, current.extrinsic_value]],
      ['Implied volatility', [current.implied_volatility]],
      ['Delta / theta', [current.delta, current.theta]],
    ];
    const grid = node('div', 'tracker-exit-market-grid');
    rows.forEach(([label, values]) => {
      const cell = node('div', 'tracker-exit-market-cell');
      add(cell, node('span', 'tracker-field-label', label));
      const holder = node('p', '', '');
      const parts = values.map(value => (
        value === null || value === undefined || value === ''
          ? null
          : Number.isFinite(Number(value)) ? money(value) : null
      ));
      const hasAny = parts.some(Boolean);
      holder.replaceChildren(document.createTextNode(hasAny ? parts.filter(Boolean).join(' / ') : ''));
      if (!hasAny) holder.append(blank(), document.createTextNode(' not available'));
      cell.append(holder);
      grid.append(cell);
    });
    add(section, grid, node('p', 'tracker-monitor-warning', `${current.status === 'verified_current' ? 'Verified current quote.' : 'No verified current quote; every current field stays blank rather than estimated.'} ${context.currency_rule || ''} ${context.greeks_note || ''}`.trim()));
    if (snapshot) {
      const stale = snapshot.status_for_current_exit_decision !== 'current';
      const block = node('div', `tracker-exit-snapshot${stale ? ' stale' : ''}`);
      add(block,
        node('span', 'tracker-field-label', 'Last verified market snapshot'),
        node('strong', stale ? 'tracker-exit-stale-badge' : '', stale ? `Stale — as of ${snapshot.as_of}` : `Current — as of ${snapshot.as_of}`),
        node('p', '', `KDP close ${money(snapshot.underlying_close, '—')} (${snapshot.underlying_close_date || snapshot.as_of}) · ${snapshot.option_symbol || 'exact contract'} bid ${money(snapshot.option_bid, '—')} ask ${money(snapshot.option_ask, '—')} spread ${money(snapshot.option_spread, '—')} · open interest ${integer(snapshot.open_interest)} · volume ${integer(snapshot.volume)} · IV ${Number.isFinite(Number(snapshot.implied_volatility)) ? Number(snapshot.implied_volatility).toFixed(4) : '—'}`),
        node('p', 'tracker-monitor-note', stale ? 'This quote is dated context only. It cannot stand in for current P&L or an exit price.' : 'Quoted on the decision day; usable as current context.'));
      if (snapshot.source_url) {
        const anchor = node('a', '', 'Quote source');
        anchor.href = snapshot.source_url;
        anchor.target = '_blank';
        anchor.rel = 'noreferrer';
        block.append(anchor);
      }
      section.append(block);
    }
    return section;
  }

  function exitSourceHealth(exit) {
    const health = exit?.data_health || {};
    const section = node('section', 'tracker-exit-health');
    add(section, node('span', 'tracker-field-label', 'Source health — latest attempt vs last complete'));
    const scroll = node('div', 'tracker-exit-table-scroll');
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const header = document.createElement('tr');
    ['Sensor', 'Latest attempt', 'Last complete', 'Next run', 'Source gap'].forEach(label => header.append(node('th', '', label)));
    head.append(header);
    const body = document.createElement('tbody');
    list(health.sensors).forEach(sensor => {
      const tr = document.createElement('tr');
      add(tr,
        node('td', '', `${sensor?.sensor || 'sensor'} · ${sensor?.operational_state || 'unknown'}`),
        node('td', '', `${timestamp(sensor?.latest_attempt_at)} — ${sensor?.latest_attempt_result || 'unknown'}`),
        node('td', '', `${timestamp(sensor?.last_complete_at)} — ${sensor?.last_complete_result || 'none yet'}`),
        node('td', '', sensor?.next_run_at ? timestamp(sensor.next_run_at) : '—'),
        node('td', '', sensor?.source_gap || 'none'));
      body.append(tr);
    });
    table.append(head, body);
    scroll.append(table);
    section.append(scroll);
    const separation = health.job_source_separation || {};
    if (separation.job !== undefined) {
      add(section, node('p', 'tracker-monitor-warning', `Job completion is separate from source success: the ${String(separation.job || 'scheduled job').replaceAll('_', ' ')} last ran ${timestamp(separation.job_last_run_at)} with status ${separation.job_last_status} (job completed: ${separation.job_completed ? 'yes' : 'no'}; source success: ${separation.source_success ? 'yes' : 'no'}). ${separation.note || ''}`));
    }
    const jobs = node('ul', 'tracker-exit-jobs');
    list(health.monitor_job_states).forEach(job => {
      jobs.append(node('li', '', `${String(job?.job || '').replaceAll('_', ' ')}: ${job?.state || 'unknown'}${job?.next_run_at ? `, next ${timestamp(job.next_run_at)}` : ''} — ${job?.note || ''}`));
    });
    section.append(jobs);
    return section;
  }

  function exitMonitorPanel(exit) {
    if (!exit || typeof exit !== 'object') return null;
    const panel = node('div', 'tracker-exit-monitor');
    add(panel,
      exitDecisionBanner(exit),
      exitPositionClock(exit),
      exitRiskBoundaries(exit),
      exitTriggerMatrix(exit),
      exitThesisChain(exit),
      exitParity(exit),
      exitMarketContext(exit),
      exitSourceHealth(exit));
    return panel;
  }

  function standingMonitorPanel(item) {
    const monitor = item?.monitor_dashboard;
    if (!monitor || typeof monitor !== 'object') return null;
    const panel = node('section', 'tracker-monitor-dashboard');
    const head = node('header', 'tracker-monitor-dashboard-head');
    const copy = node('div');
    const realization = monitor?.thesis_realization || {};
    const realizationStatus = String(realization?.status || 'BUILDING_BASELINE').toUpperCase();
    const realizationLabel = {
      BUILDING_BASELINE: 'Building baseline',
      CONTINUE_MONITORING: 'Continue monitoring',
      EXIT_REVIEW: 'Exit review',
    }[realizationStatus] || realizationStatus.replaceAll('_', ' ').toLowerCase();
    add(
      copy,
      node('span', 'tracker-field-label', 'Thesis realization'),
      node('h4', '', realization?.current_read || 'Monitoring evidence is loading.'),
      node('p', 'tracker-monitor-note', `Latest retail change: ${monitor?.headline || 'not available.'}`),
    );
    const status = node('div', 'tracker-realization-status');
    add(
      status,
      node('strong', `tracker-realization-badge ${realizationStatus.toLowerCase().replaceAll('_', '-')}`, realizationLabel),
      node('span', 'tracker-monitor-asof mono', `Updated ${timestamp(monitor?.as_of)}`),
    );
    add(head, copy, status);
    panel.append(head);
    const grid = node('div', 'tracker-monitor-grid');
    add(grid, availabilityHistory(monitor?.availability), searchAttentionPanel(monitor?.search), conversationPanel(monitor?.conversations), streetCoveragePanel(monitor?.street_coverage));
    panel.append(grid);
    const exitPanel = exitMonitorPanel(monitor?.exit_monitor);
    if (exitPanel) {
      const exitDetails = node('details', 'tracker-exit-disclosure');
      exitDetails.append(node('summary', '', 'Position and exit-review details'), exitPanel);
      panel.append(exitDetails);
    }
    const receipts = monitor?.source_receipts || {};
    const audit = node('details', 'tracker-monitor-audit');
    audit.append(node('summary', '', `Why this is real data · ${integer(list(receipts?.artifacts).length)} persisted source receipts`));
    audit.append(node('p', 'tracker-monitor-note', `Opening or refreshing this dashboard made ${integer(receipts?.upstream_calls)} upstream source calls. Collection happens separately and is preserved with timestamps and hashes.`));
    const attempts = list(receipts?.operational_attempts);
    if (attempts.length) {
      const attemptList = node('div', 'tracker-monitor-attempts');
      attemptList.append(node('span', 'tracker-field-label', 'Latest source attempts'));
      attempts.forEach(attempt => {
        const status = String(attempt?.status || 'unknown').replaceAll('_', ' ');
        attemptList.append(node('p', 'tracker-monitor-note', `${attempt?.source || 'Source'} · ${status} · ${timestamp(attempt?.observed_at)} · ${attempt?.usable ? 'used in the visible result' : 'kept out of the visible result'}`));
      });
      audit.append(attemptList);
    }
    const receiptList = node('div', 'tracker-monitor-receipts');
    list(receipts?.artifacts).forEach(receipt => {
      receiptList.append(node('p', 'mono', `${receipt?.path || 'Source'} · ${String(receipt?.sha256 || '').slice(0, 12)} · ${timestamp(receipt?.modified_at)}`));
    });
    audit.append(receiptList);
    panel.append(audit);
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
    const exitState = item?.monitor_dashboard?.exit_monitor?.decision_banner?.review_state;
    if (exitState && !['DATA_INCOMPLETE', 'NO_EXIT_TRIGGER_VERIFIED'].includes(String(exitState).toUpperCase())) {
      heading.append(node('span', `tracker-exit-state-badge ${String(exitState).toLowerCase().replaceAll('_', '-')}`, exitStateLabel(exitState)));
    }
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

    const monitorDashboard = standingMonitorPanel(item);
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
    add(article, status, thesis, action, monitorDashboard, trend, detail);
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
