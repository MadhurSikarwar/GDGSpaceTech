// Chart.js wrappers. Colours come from CSS tokens (categorical slots for
// object types and regions, status colours only for risk), so light and dark
// mode each use their own validated steps. Thin marks, rounded data ends,
// recessive grid, a legend whenever there is more than one series, and a
// table view beside every chart for exact values.

const live = new Set();

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// Categorical order (fixed, never cycled): validated reference palette.
const SLOTS_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
const SLOTS_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
const dark = () => css('color-scheme') === 'dark' || getComputedStyle(document.documentElement).colorScheme === 'dark';
export const slot = (i) => (dark() ? SLOTS_DARK : SLOTS_LIGHT)[i];
export const typeColor = (t) => css({ 'Payload': '--t-payload', 'Rocket Body': '--t-rocket', 'Debris': '--t-debris' }[t]
  || '--t-unknown');

function base(horizontal = false, stacked = false, xTitle = '', yTitle = '', showLegend = false) {
  const ink2 = css('--ink-2');
  const ink3 = css('--ink-3');
  const grid = css('--border');
  const axis = (title, isValue) => ({
    stacked,
    grid: { display: isValue, color: grid, drawTicks: false },
    border: { display: !isValue, color: css('--border-strong') },
    ticks: { color: ink3, font: { family: 'Inter', size: 11.5 }, padding: 6 },
    title: { display: !!title, text: title, color: ink3, font: { family: 'Inter', size: 11.5 } },
  });
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    indexAxis: horizontal ? 'y' : 'x',
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: showLegend, position: 'top', align: 'start',
        labels: { color: ink2, boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 3,
          font: { family: 'Inter', size: 12 } } },
      tooltip: { backgroundColor: css('--ink'), titleColor: css('--surface'), bodyColor: css('--surface'),
        titleFont: { family: 'Inter', weight: '600' }, bodyFont: { family: 'Inter' }, padding: 10,
        boxPadding: 4, cornerRadius: 6, usePointStyle: true },
    },
    scales: horizontal ? { x: axis(xTitle, true), y: axis(yTitle, false) } : { x: axis(xTitle, false), y: axis(yTitle, true) },
  };
}

export function barChart(canvas, { labels, series, horizontal = false, stacked = false, xTitle = '', yTitle = '' }) {
  const surface = css('--surface');
  const chart = new window.Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: series.map((s) => ({
        label: s.label, data: s.data, backgroundColor: s.color, hoverBackgroundColor: s.color,
        borderRadius: stacked ? 2 : 4, borderSkipped: false,
        borderColor: surface, borderWidth: stacked ? 1 : 0,       // 2px surface gap between stacked fills
        maxBarThickness: 28, categoryPercentage: 0.8, barPercentage: 0.9,
      })),
    },
    options: base(horizontal, stacked, xTitle, yTitle, series.length > 1),
  });
  live.add(chart);
  return chart;
}

export function lineChart(canvas, { series, yTitle = '' }) {
  // x values are epoch milliseconds; a linear axis with date tick labels avoids a date adapter.
  const opts = base(false, false, '', yTitle, series.length > 1);
  opts.scales.x = { ...opts.scales.x, type: 'linear',
    ticks: { ...opts.scales.x.ticks, maxTicksLimit: 8, callback: (v) => new Date(v).toISOString().slice(0, 10) } };
  opts.plugins.tooltip.callbacks = {
    title: (items) => new Date(items[0].parsed.x).toISOString().replace('T', ' ').slice(0, 16) + ' UTC',
  };
  opts.interaction = { mode: 'nearest', axis: 'x', intersect: false };
  const chart = new window.Chart(canvas, {
    type: 'line',
    data: {
      datasets: series.map((s) => ({
        label: s.label, data: s.points, borderColor: s.color, backgroundColor: s.color,
        borderWidth: 2, pointRadius: s.points.length > 60 ? 0 : 3, pointHoverRadius: 5, tension: 0,
      })),
    },
    options: opts,
  });
  live.add(chart);
  return chart;
}

export function destroyAll() {
  for (const c of live) c.destroy();
  live.clear();
}

export function legendHtml(items) {
  return `<div class="legend">${items.map(([label, color]) =>
    `<span><i style="background:${color}"></i>${label}</span>`).join('')}</div>`;
}
