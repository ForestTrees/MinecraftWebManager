// Minimal dependency-free SVG line chart renderer (no build step, no CDN).
// Nulls in a series are rendered as gaps rather than being interpolated over.
(function () {
  const PAD = { left: 58, right: 14, top: 12, bottom: 22 };
  const DEFAULT_HEIGHT = 190;

  function escapeHtml(text) {
    const node = document.createElement('span');
    node.textContent = String(text);
    return node.innerHTML;
  }

  function niceMax(value) {
    if (!isFinite(value) || value <= 0) return 1;
    const exponent = Math.floor(Math.log10(value));
    const base = Math.pow(10, exponent);
    const normalized = value / base;
    const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
    return step * base;
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  function formatClock(seconds, spanSeconds) {
    const date = new Date(seconds * 1000);
    const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    return spanSeconds <= 12 * 3600 ? time : `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${time}`;
  }

  function formatStamp(seconds, spanSeconds) {
    const date = new Date(seconds * 1000);
    const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    return spanSeconds <= 3600 ? clock : `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${clock}`;
  }

  // One path per contiguous run of non-null points so gaps stay gaps.
  function buildPaths(values, xFor, yFor, baseline) {
    const lines = [];
    const areas = [];
    let run = [];
    const flush = () => {
      if (!run.length) return;
      const line = run.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
      lines.push(line);
      if (run.length > 1) {
        const first = run[0], last = run[run.length - 1];
        areas.push(`M${first.x.toFixed(1)},${baseline.toFixed(1)} ${line.slice(1)} L${last.x.toFixed(1)},${baseline.toFixed(1)} Z`);
      }
      run = [];
    };
    values.forEach((value, index) => {
      if (value == null || !isFinite(value)) { flush(); return; }
      run.push({ x: xFor(index), y: yFor(value) });
    });
    flush();
    return { lines, areas };
  }

  function tooltipFor(svg) {
    const host = svg.parentElement;
    if (!host) return null;
    let tip = host.querySelector('.chart-tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'chart-tooltip';
      tip.hidden = true;
      host.appendChild(tip);
    }
    return tip;
  }

  function ensureInteraction(svg) {
    if (svg.__chartBound) return;
    svg.__chartBound = true;
    svg.addEventListener('mousemove', (event) => {
      const chart = svg.__chart;
      if (!chart || !chart.geometry) return;
      const { plotLeft, plotWidth, count } = chart.geometry;
      const rect = svg.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const ratio = plotWidth > 0 ? (x - plotLeft) / plotWidth : 0;
      const index = Math.round(Math.min(1, Math.max(0, ratio)) * count);
      if (index !== chart.hoverIndex) {
        chart.hoverIndex = index;
        draw(svg);
      }
      positionTooltip(svg, x);
    });
    svg.addEventListener('mouseleave', () => {
      const chart = svg.__chart;
      if (!chart || chart.hoverIndex == null) return;
      chart.hoverIndex = null;
      draw(svg);
      const tip = tooltipFor(svg);
      if (tip) tip.hidden = true;
    });
  }

  function positionTooltip(svg, x) {
    const tip = tooltipFor(svg);
    if (!tip || tip.hidden) return;
    const hostWidth = svg.parentElement.clientWidth;
    const width = tip.offsetWidth || 140;
    const left = Math.min(Math.max(x, width / 2 + 4), hostWidth - width / 2 - 4);
    tip.style.left = `${left}px`;
  }

  function draw(svg) {
    const chart = svg.__chart;
    if (!chart) return false;
    const { timestamps = [], series = [], formatValue = (v) => String(v), spanSeconds = 3600 } = chart;
    const height = chart.height || DEFAULT_HEIGHT;
    const width = Math.round(svg.clientWidth || (svg.parentElement && svg.parentElement.clientWidth) || 0);

    // A hidden panel measures 0 wide; skip rather than render a broken chart.
    if (width < 80) return false;

    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('height', String(height));
    svg.setAttribute('preserveAspectRatio', 'none');

    const tip = tooltipFor(svg);
    const hasData = series.some((s) => s.values.some((v) => v != null && isFinite(v)));
    if (!hasData) {
      const emptyText = window.MWMI18N ? window.MWMI18N.t('chart_no_data') : '暂无数据';
      svg.innerHTML = `<text x="${width / 2}" y="${height / 2}" text-anchor="middle" dominant-baseline="middle" class="chart-empty">${escapeHtml(emptyText)}</text>`;
      chart.geometry = null;
      if (tip) tip.hidden = true;
      return true;
    }

    let maxValue = 0;
    series.forEach((s) => s.values.forEach((v) => { if (v != null && isFinite(v) && v > maxValue) maxValue = v; }));
    const yMax = chart.yMax != null ? chart.yMax : niceMax(maxValue * 1.15);
    const plotLeft = PAD.left;
    const plotRight = width - PAD.right;
    const plotTop = PAD.top;
    const plotBottom = height - PAD.bottom;
    const plotWidth = plotRight - plotLeft;
    const plotHeight = plotBottom - plotTop;
    const count = Math.max(1, timestamps.length - 1);
    const xFor = (index) => plotLeft + (index / count) * plotWidth;
    const yFor = (value) => plotBottom - Math.min(1, Math.max(0, value / yMax)) * plotHeight;
    chart.geometry = { plotLeft, plotWidth, count };

    const parts = [];
    const GRID = 4;
    for (let i = 0; i <= GRID; i += 1) {
      const value = (yMax / GRID) * i;
      const y = yFor(value);
      parts.push(`<line x1="${plotLeft}" y1="${y.toFixed(1)}" x2="${plotRight}" y2="${y.toFixed(1)}" class="chart-grid"/>`);
      parts.push(`<text x="${plotLeft - 8}" y="${y.toFixed(1)}" text-anchor="end" dominant-baseline="middle" class="chart-axis">${escapeHtml(formatValue(value))}</text>`);
    }

    const LABELS = 4;
    for (let i = 0; i <= LABELS; i += 1) {
      const index = Math.round((timestamps.length - 1) * (i / LABELS));
      const stamp = timestamps[index];
      if (stamp == null) continue;
      const anchor = i === 0 ? 'start' : i === LABELS ? 'end' : 'middle';
      parts.push(`<text x="${xFor(index).toFixed(1)}" y="${height - 6}" text-anchor="${anchor}" class="chart-axis">${escapeHtml(formatClock(stamp, spanSeconds))}</text>`);
    }

    series.forEach((entry) => {
      const { lines, areas } = buildPaths(entry.values, xFor, yFor, plotBottom);
      areas.forEach((d) => parts.push(`<path d="${d}" style="fill:${entry.color}" class="chart-area"/>`));
      lines.forEach((d) => parts.push(`<path d="${d}" style="stroke:${entry.color}" class="chart-line"/>`));
    });

    // hover crosshair + per-series markers
    const hover = chart.hoverIndex;
    if (hover != null && hover >= 0 && hover < timestamps.length) {
      const x = xFor(hover);
      parts.push(`<line x1="${x.toFixed(1)}" y1="${plotTop}" x2="${x.toFixed(1)}" y2="${plotBottom}" class="chart-crosshair"/>`);
      const rows = [];
      series.forEach((entry) => {
        const value = entry.values[hover];
        if (value == null || !isFinite(value)) {
          const noValue = window.MWMI18N ? window.MWMI18N.t('chart_no_value') : '无数据';
          rows.push(`<span class="key"><span class="swatch" style="background:${entry.color}"></span>${escapeHtml(entry.label)}<b>${escapeHtml(noValue)}</b></span>`);
          return;
        }
        parts.push(`<circle cx="${x.toFixed(1)}" cy="${yFor(value).toFixed(1)}" r="3.5" style="fill:${entry.color}" class="chart-dot"/>`);
        rows.push(`<span class="key"><span class="swatch" style="background:${entry.color}"></span>${escapeHtml(entry.label)}<b>${escapeHtml(formatValue(value))}</b></span>`);
      });
      if (tip) {
        tip.innerHTML = `<div class="chart-tooltip-time">${escapeHtml(formatStamp(timestamps[hover], spanSeconds))}</div>${rows.join('')}`;
        tip.hidden = false;
      }
    } else if (tip) {
      tip.hidden = true;
    }

    svg.innerHTML = parts.join('');
    return true;
  }

  function render(svg, options) {
    const previous = svg.__chart;
    svg.__chart = Object.assign({}, options, { hoverIndex: previous ? previous.hoverIndex : null });
    ensureInteraction(svg);
    return draw(svg);
  }

  window.MWMChart = { render };
})();
