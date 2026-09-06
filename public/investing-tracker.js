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

  function monitorSearchSvg(history, queries) {
    const namespace = 'http://www.w3.org/2000/svg';
    const width = 720;
    const height = 210;
    const left = 48;
    const right = 704;
    const top = 18;
    const bottom = 166;
    const values = history.flatMap(point => queries.map(query => Number(point?.changes_pct?.[query])).filter(Number.isFinite));
    const ceiling = Math.max(25, ...values.map(value => value * 1.08));
    const floor = Math.min(-50, ...values.map(value => value * 1.08));
    const x = index => history.length <= 1 ? (left + right) / 2 : left + (index / (history.length - 1)) * (right - left);
    const y = value => bottom - ((value - floor) / (ceiling - floor)) * (bottom - top);
    const svg = document.createElementNS(namespace, 'svg');
    svg.setAttribute('class', 'tracker-attention-chart');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Rolling seven-day Google search change versus the previous seven days');
    [[-25, 'Cooling review line'], [0, 'No change versus prior seven days']].forEach(([value, label]) => {
      const guide = document.createElementNS(namespace, 'line');
      guide.setAttribute('class', `tracker-attention-guide${value === -25 ? ' cooling' : ''}`);
      guide.setAttribute('x1', String(left)); guide.setAttribute('x2', String(right));
      guide.setAttribute('y1', String(y(value))); guide.setAttribute('y2', String(y(value)));
      const text = document.createElementNS(namespace, 'text');
      text.setAttribute('class', 'tracker-trend-axis-label');
      text.setAttribute('x', '2'); text.setAttribute('y', String(y(value) + 4));
      text.textContent = `${value}%`;
      const title = document.createElementNS(namespace, 'title');
      title.textContent = label;
      guide.append(title);
      svg.append(guide, text);
    });
    queries.forEach((query, queryIndex) => {
      let segment = [];
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
      history.forEach((point, index) => {
        const value = Number(point?.changes_pct?.[query]);
        if (!Number.isFinite(value)) {
          flush();
          return;
        }
        const coordinate = { x: x(index), y: y(value), value, point };
        segment.push(coordinate);
        const circle = document.createElementNS(namespace, 'circle');
        circle.setAttribute('class', `tracker-attention-point query-${queryIndex + 1}`);
        circle.setAttribute('cx', String(coordinate.x)); circle.setAttribute('cy', String(coordinate.y)); circle.setAttribute('r', '4');
        const title = document.createElementNS(namespace, 'title');
        title.textContent = `${query}: ${value >= 0 ? '+' : ''}${value.toFixed(1)}% through ${axisDate(point?.date)}`;
        circle.append(title);
        svg.append(circle);
      });
      flush();
    });
    const tickIndexes = new Set([0, Math.floor((history.length - 1) / 2), history.length - 1]);
    history.forEach((point, index) => {
      if (!tickIndexes.has(index)) return;
      const text = document.createElementNS(namespace, 'text');
      text.setAttribute('class', 'tracker-trend-date-label');
      text.setAttribute('x', String(x(index))); text.setAttribute('y', '194');
      text.setAttribute('text-anchor', index === 0 ? 'start' : index === history.length - 1 ? 'end' : 'middle');
      text.textContent = axisDate(point?.date);
      svg.append(text);
    });
    return svg;
  }

  function searchAttentionPanel(search) {
    const section = node('section', 'tracker-monitor-section tracker-search-attention');
    add(
      section,
      node('span', 'tracker-field-label', 'Search attention change'),
      node('h5', '', 'Rolling 7-day search change'),
      node('p', 'tracker-monitor-lead', search?.current_read || 'Search direction unavailable.'),
    );
    const history = list(search?.rolling_seven_day_change);
    const queries = list(search?.query_basket).length
      ? list(search.query_basket)
      : [...new Set(history.flatMap(point => Object.keys(point?.changes_pct || {})))];
    if (history.length && queries.length) {
      const scroll = node('div', 'tracker-attention-chart-scroll');
      scroll.append(monitorSearchSvg(history, queries));
      section.append(scroll);
      const legend = node('div', 'tracker-monitor-legend');
      queries.forEach((query, index) => {
        const item = node('span', '');
        add(item, node('i', `tracker-legend-key query-${index + 1}`), document.createTextNode(query));
        legend.append(item);
      });
      section.append(legend);
      section.append(node('p', 'tracker-monitor-note', 'Each point compares the latest 7 complete days with the previous 7 days inside the same Google request. Failed and partial dates are omitted, not shown as zero.'));
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
          row.append(node('td', '', Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` : 'Missing'));
        });
        body.append(row);
      });
      table.append(head, body);
      details.append(table);
      section.append(details);
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
    const values = rows.flatMap(row => [Number(row?.[options.primaryKey] || 0), Number(row?.[options.secondaryKey] || 0)]);
    const ceiling = Math.max(1, ...values);
    const chart = node('div', 'tracker-count-chart');
    chart.setAttribute('role', 'img');
    chart.setAttribute('aria-label', options.ariaLabel);
    rows.forEach(reading => {
      const day = node('div', 'tracker-count-day');
      const bars = node('div', 'tracker-count-bars');
      [[options.primaryKey, 'primary'], [options.secondaryKey, 'secondary']].forEach(([key, className]) => {
        const value = Number(reading?.[key] || 0);
        const bar = node('span', `tracker-count-bar ${className}`);
        bar.style.height = `${Math.max(value ? 5 : 0, (value / ceiling) * 100)}%`;
        bar.title = `${options.labels[className]}: ${value}`;
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
    add(block, chart, legend);
    return block;
  }

  function conversationPanel(conversations) {
    const section = node('section', 'tracker-monitor-section tracker-conversation-panel');
    add(
      section,
      node('span', 'tracker-field-label', 'Observed conversation volume'),
      node('h5', '', conversations?.headline || `${integer(conversations?.exact_roots)} exact posts successfully observed`),
      node('p', 'tracker-monitor-lead', conversations?.current_read || 'Conversation direction unavailable.'),
      node('p', 'tracker-monitor-note', `${integer(conversations?.exact_roots)} exact posts found · ${integer(conversations?.qualifying_roots)} independently qualifying.`),
    );
    section.append(countHistoryBlock(conversations?.history, {
      title: 'Observed conversation volume over time',
      className: 'tracker-buzz-history',
      primaryKey: 'exact_roots',
      secondaryKey: 'qualifying_roots',
      labels: { primary: 'Exact posts', secondary: 'Independent posts' },
      ariaLabel: 'Exact and independently qualifying conversation posts by monitoring date',
      emptyCopy: 'No dated conversation-volume history yet.',
    }));
    section.append(node('p', 'tracker-monitor-note', conversations?.coverage_note || 'Observed volume is shown from successful sources.'));
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
      add(sentimentBlock, sentimentGrid, node('p', 'tracker-monitor-note', sentiment.note || 'Positive and negative both count toward buzz.'));
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
      add(
        row,
        node('strong', '', platformLabel),
        node('span', '', 'Observed'),
        node('span', '', queryLabel),
        node('span', 'mono', `${integer(reading?.exact_roots)} exact · ${integer(reading?.qualifying_roots)} independent`),
      );
      grid.append(row);
    });
    section.append(grid);
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
    section.append(countHistoryBlock(coverage?.history, {
      title: 'News and management coverage over time',
      className: 'tracker-news-history',
      primaryKey: 'qualifying_outlets',
      secondaryKey: 'management_acknowledged',
      labels: { primary: 'Qualifying outlets', secondary: 'Management acknowledgment' },
      ariaLabel: 'Qualifying financial outlets and management acknowledgment by monitoring date',
      emptyCopy: 'No dated financial-news coverage history yet.',
    }));
    const checks = coverage?.source_checks || {};
    section.append(node('p', 'tracker-monitor-note', [
      `${integer(checks.official_sources)} official IR sources`,
      `${integer(checks.sec_filings)} SEC filings`,
      `${integer(checks.news_queries)} news searches`,
      checks.earnings_call_or_transcript_checked ? 'Earnings calls checked' : 'Earnings-call transcript not yet verified',
    ].join(' · ')));
    return section;
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
    const receipts = monitor?.source_receipts || {};
    const audit = node('details', 'tracker-monitor-audit');
    audit.append(node('summary', '', `Why this is real data · ${integer(list(receipts?.artifacts).length)} persisted source receipts`));
    audit.append(node('p', 'tracker-monitor-note', `Opening or refreshing this dashboard made ${integer(receipts?.upstream_calls)} upstream source calls. Collection happens separately and is preserved with timestamps and hashes.`));
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
