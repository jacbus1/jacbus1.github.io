'use strict';

const $ = id => document.getElementById(id);
const strategies = [
  { id: 'fixed_weights', name: 'Fixed 60/30/10', color: '#ff9e91' },
  { id: 'es_overlay', name: 'ES overlay', color: '#7ce0b7' },
  { id: 'matched_static_cash', name: 'Static cash diagnostic', color: '#9bbaff' },
];
const stressNames = {
  global_financial_crisis: 'Global financial crisis',
  pandemic_selloff: 'COVID-19 selloff',
  pandemic_rebound_through_q2: 'COVID-19 rebound through Q2',
  inflation_and_rates_2022: '2022 inflation and rate shock',
};
const number = new Intl.NumberFormat('en', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const percent = value => Number.isFinite(value) ? number.format(value * 100) + '%' : '—';
const signedPoints = value => (value >= 0 ? '+' : '') + number.format(value * 100) + ' pp';
const count = value => Number.isFinite(value) ? value.toLocaleString('en') : '—';

function tag(name, content, className) {
  const node = document.createElement(name);
  if (content != null) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function dataList(rows) {
  const list = tag('dl');
  for (const [label, value] of rows) {
    const row = tag('div');
    row.append(tag('dt', label), tag('dd', value));
    list.append(row);
  }
  return list;
}

async function load(filename) {
  const response = await fetch('results/' + filename);
  if (!response.ok) throw new Error('Artifact unavailable');
  return response.json();
}

function metricsTable(rows) {
  const headings = tag('tr');
  const metricHeading = tag('th', 'Measure');
  metricHeading.scope = 'col';
  headings.append(metricHeading);
  for (const { name, id } of strategies) {
    const heading = tag('th', name, id === 'es_overlay' ? 'selected-model' : undefined);
    heading.scope = 'col';
    headings.append(heading);
  }
  $('model-head').replaceChildren(headings);

  const measures = [
    ['cagr', 'Annualized return'],
    ['annualized_volatility', 'Annualized volatility'],
    ['max_drawdown', 'Maximum drawdown'],
    ['daily_var_95', 'Daily 95% VaR'],
    ['daily_expected_shortfall_95', 'Daily 95% ES'],
    ['average_cash_weight', 'Average cash weight'],
    ['annualized_gross_turnover', 'Annualized gross turnover'],
  ];
  $('model-body').replaceChildren(...measures.map(([field, label]) => {
    const row = tag('tr');
    const heading = tag('th', label);
    heading.scope = 'row';
    row.append(heading, ...strategies.map(({ id }) => tag('td', percent(rows[id][field]))));
    return row;
  }));
}

function showStress(stress) {
  const select = $('stress-case');
  select.replaceChildren();
  for (const [id, label] of Object.entries(stressNames)) if (stress[id]) {
    const option = tag('option', label);
    option.value = id;
    select.append(option);
  }
  if (stress.pandemic_selloff) select.value = 'pandemic_selloff';
  const render = () => {
    const item = stress[select.value];
    if (!item) return;
    const returns = strategies.map(({ id, name }) => [name, percent(item.strategies[id]?.return)]);
    $('stress-output').replaceChildren(
      tag('p', item.first_session + ' to ' + item.last_session + ' · ' + item.period +
        ' · SPY/IEF return correlation ' + number.format(item.spy_ief_daily_return_correlation), 'note'),
      dataList(returns)
    );
  };
  select.addEventListener('change', render);
  render();
}

function showRisk(latest) {
  const row = latest.strategies.es_overlay;
  $('risk-output').replaceChildren(
    tag('p', 'Overlay holdings · ' + latest.as_of_session + ' close · ' + count(row.observations) + ' trailing sessions', 'note'),
    dataList([
      ['Daily 95% Value at Risk', percent(row.var_95)],
      ['Daily 95% Expected Shortfall', percent(row.expected_shortfall_95)],
      ['Estimated annualized volatility', percent(row.annualized_volatility)],
      ['Cash weight', percent(row.cash_weight)],
    ]),
    tag('p', 'Expected Shortfall estimates the average loss in the historical tail. VaR and ES can understate losses in a new regime.', 'note')
  );
}

function showCashSensitivity(sensitivity) {
  const select = $('cash-yield');
  select.replaceChildren(...Object.keys(sensitivity).sort((a, b) => Number(a) - Number(b)).map(rate => {
    const option = tag('option', rate + '%');
    option.value = rate;
    return option;
  }));
  const render = () => {
    const result = sensitivity[select.value];
    $('cash-output').replaceChildren(dataList(strategies.map(({ id, name }) => [
      name, percent(result[id].cagr) + ' return · ' + percent(result[id].max_drawdown) + ' drawdown',
    ])));
  };
  select.addEventListener('change', render);
  render();
}

function showChart(curves) {
  const canvas = $('history-chart');
  const select = $('chart-view');
  $('chart-legend').replaceChildren(...strategies.map(({ name, color }) => {
    const label = tag('span', name);
    const line = tag('i');
    line.style.background = color;
    label.prepend(line);
    return label;
  }));
  function draw() {
    const view = select.value;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height || curves.length < 2) return;
    const pixelRatio = window.devicePixelRatio || 1;
    canvas.width = width * pixelRatio;
    canvas.height = height * pixelRatio;
    const context = canvas.getContext('2d');
    if (!context) return;
    context.scale(pixelRatio, pixelRatio);
    const values = curves.flatMap(row => strategies.map(({ id }) => row[id][view]));
    const bottom = Math.min(...values, view === 'nav' ? 1 : 0);
    const top = Math.max(...values, view === 'nav' ? 1.01 : .001);
    const left = 53, right = width - 13, high = 14, low = height - 25;
    const x = i => left + (right - left) * i / (curves.length - 1);
    const y = n => low - (low - high) * (n - bottom) / (top - bottom);
    context.font = '11px Arial';
    context.fillStyle = '#abb4c3';
    for (let i = 0; i < 5; i++) {
      const value = bottom + i * (top - bottom) / 4;
      context.strokeStyle = '#2d3540';
      context.beginPath();
      context.moveTo(left, y(value));
      context.lineTo(right, y(value));
      context.stroke();
      context.fillText(view === 'nav' ? '$' + value.toFixed(2) : (value * 100).toFixed(1) + '%', 0, y(value) + 4);
    }
    for (const { id, color } of strategies) {
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.beginPath();
      curves.forEach((row, i) => {
        const value = row[id][view];
        if (i === 0) context.moveTo(x(i), y(value)); else context.lineTo(x(i), y(value));
      });
      context.stroke();
    }
    context.fillText(curves[0].date.slice(0, 4), left, height - 4);
    context.fillText(curves.at(-1).date.slice(0, 4), right - 24, height - 4);
    canvas.setAttribute('aria-label', (view === 'nav' ? 'Growth of one dollar' : 'Drawdown') +
      ' for the three strategies by month. Summary values are in the preceding table.');
  }
  select.addEventListener('change', draw);
  new ResizeObserver(draw).observe(canvas);
  draw();
}

function showBacktest(report) {
  const holdout = report.holdout;
  const rows = holdout.strategies;
  const fixed = rows.fixed_weights;
  const overlay = rows.es_overlay;
  const cash = rows.matched_static_cash;
  $('backtest-summary').textContent = holdout.first_session + ' to ' + holdout.last_session +
    ' · ' + count(fixed.sessions) + ' trading sessions. Realized daily 95% ES was ' +
    percent(overlay.daily_expected_shortfall_95) + ' with a ' + percent(report.methodology.risk_daily_es_limit) +
    ' budget at monthly rebalances. The static cash allocation was fixed using the development period.';
  const cards = [
    ['Drawdown change vs. fixed', signedPoints(overlay.max_drawdown - fixed.max_drawdown), percent(fixed.max_drawdown) + ' to ' + percent(overlay.max_drawdown)],
    ['Annualized return change vs. fixed', signedPoints(overlay.cagr - fixed.cagr), percent(fixed.cagr) + ' to ' + percent(overlay.cagr)],
    ['Annualized return vs. static cash', signedPoints(overlay.cagr - cash.cagr), percent(cash.cagr) + ' to ' + percent(overlay.cagr)],
  ];
  $('metrics').replaceChildren(...cards.map(([label, value, detail]) => {
    const card = tag('article');
    card.append(tag('small', label), tag('strong', value), tag('small', detail));
    return card;
  }));
  metricsTable(rows);
  showStress(report.historical_stress);
  showRisk(report.latest_risk);
  showCashSensitivity(report.holdout_cash_yield_sensitivity_pct);
  const method = report.methodology;
  $('assumptions').append(
    tag('li', method.execution_rule),
    tag('li', method.risk_lookback_sessions + ' sessions per risk window. Cash earns ' + percent(method.cash_annual_yield) + ' per year in the simulation.'),
    tag('li', method.transaction_fee_bps_per_absolute_traded_dollar + ' bps per absolute dollar traded in the main comparison.'),
    tag('li', 'Vendor-adjusted historical prices. The public artifact contains derived portfolio results, with no source price rows or client data.')
  );
  $('metrics').hidden = false;
  $('backtest-details').hidden = false;
  showChart(report.holdout_monthly_curve);
}

function showSandbox(cases) {
  const select = $('request-case');
  select.replaceChildren();
  for (const entry of cases) {
    const option = tag('option', entry.label);
    option.value = entry.id;
    select.append(option);
  }
  const render = () => {
    const row = cases.find(item => item.id === select.value);
    if (!row) return;
    $('request-in').textContent = JSON.stringify(row.request, null, 2);
    $('request-out').textContent = JSON.stringify(row.response, null, 2);
    $('request-result').textContent = row.summary;
    $('request-result').dataset.status = row.status;
  };
  select.addEventListener('change', render);
  render();
}

function showReview(report) {
  const output = $('review-output');
  output.replaceChildren(...report.reviewers.map(row => {
    const card = tag('article');
    const list = tag('ul');
    list.append(...row.findings.map(finding => tag('li', finding)));
    card.append(tag('h3', row.model), tag('p', row.role + ' · ' + row.score, 'note'), list);
    return card;
  }));
}

load('backtest.json').then(showBacktest).catch(() => {
  $('backtest-summary').textContent = 'The backtest could not be loaded. See the downloadable results and methods.';
});
load('bloomberg.json').then(showSandbox).catch(() => {
  $('request-result').textContent = 'Mock results could not be loaded.';
  $('request-case').disabled = true;
});
load('review.json').then(showReview).catch(() => {
  $('review-output').textContent = 'The review summary could not be loaded.';
});
