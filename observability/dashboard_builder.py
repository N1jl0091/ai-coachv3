"""
Build the static observability dashboard.

Generates two files into `docs/`:
  - `logs.json`  — raw metrics + recent events as JSON
  - `index.html` — single self-contained page that fetches `logs.json`
                   and renders charts via Chart.js (CDN)

The HTML is hand-written, dark engineering-ops aesthetic: monospace
typography, sharp grid, no marketing fluff. Single source-of-truth for the
"is the bot healthy?" question.

Called by `observability.flush.flush_dashboard` on a schedule (and on demand).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import settings
from db.logs import fetch_dashboard_metrics

logger = logging.getLogger(__name__)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AI Coach · Observability</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0b0d10;
    --bg-2: #11151a;
    --panel: #141a21;
    --panel-2: #1a2128;
    --border: #243039;
    --fg: #e6edf3;
    --fg-dim: #8b96a3;
    --fg-mute: #5b6672;
    --accent: #6ee7b7;
    --accent-2: #60a5fa;
    --warn: #fbbf24;
    --err: #f87171;
    --crit: #f43f5e;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
  body {
    font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px;
    line-height: 1.55;
    min-height: 100vh;
  }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { text-decoration: underline; }

  .topbar {
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #0e1217 0%, #0b0d10 100%);
    padding: 18px 28px;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }
  .brand {
    font-family: "Space Grotesk", system-ui, sans-serif;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.4px;
  }
  .brand .dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent);
    margin-right: 10px;
    box-shadow: 0 0 12px var(--accent);
    transform: translateY(-2px);
  }
  .meta {
    color: var(--fg-dim);
    font-size: 12px;
  }
  .meta strong { color: var(--fg); font-weight: 500; }

  .container {
    padding: 24px 28px 60px;
    max-width: 1400px;
    margin: 0 auto;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 14px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px 18px;
    overflow: hidden;
  }
  .card h3 {
    margin: 0 0 12px;
    font-size: 11px;
    font-weight: 500;
    color: var(--fg-dim);
    text-transform: uppercase;
    letter-spacing: 1.4px;
  }
  .stat {
    font-family: "Space Grotesk", system-ui, sans-serif;
    font-size: 26px;
    font-weight: 700;
    color: var(--fg);
    line-height: 1;
  }
  .stat .unit {
    font-size: 13px;
    color: var(--fg-dim);
    margin-left: 6px;
    font-weight: 500;
  }
  .stat-sub {
    margin-top: 6px;
    color: var(--fg-mute);
    font-size: 11px;
    letter-spacing: 0.4px;
  }

  .col-3 { grid-column: span 3; }
  .col-4 { grid-column: span 4; }
  .col-6 { grid-column: span 6; }
  .col-8 { grid-column: span 8; }
  .col-12 { grid-column: span 12; }

  @media (max-width: 1100px) {
    .col-3, .col-4 { grid-column: span 6; }
    .col-6, .col-8 { grid-column: span 12; }
  }
  @media (max-width: 640px) {
    .col-3, .col-4, .col-6, .col-8 { grid-column: span 12; }
    .container { padding: 16px; }
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th, td {
    text-align: left;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  th {
    color: var(--fg-dim);
    font-weight: 500;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 1px;
  }
  td { color: var(--fg); }
  td.dim { color: var(--fg-mute); }
  tr:last-child td { border-bottom: none; }

  .pill {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    border: 1px solid var(--border);
    color: var(--fg-dim);
  }
  .sev-info { color: var(--accent); border-color: rgba(110,231,183,.3); }
  .sev-warning, .sev-warn { color: var(--warn); border-color: rgba(251,191,36,.3); }
  .sev-error { color: var(--err); border-color: rgba(248,113,113,.3); }
  .sev-critical { color: var(--crit); border-color: rgba(244,63,94,.4); }

  .chart-wrap {
    position: relative;
    height: 220px;
  }
  .chart-wrap.tall { height: 260px; }

  .empty {
    color: var(--fg-mute);
    font-size: 12px;
    text-align: center;
    padding: 20px 0;
  }

  .footer {
    margin-top: 28px;
    color: var(--fg-mute);
    font-size: 11px;
    text-align: right;
    letter-spacing: 0.4px;
  }
</style>
</head>
<body>
  <div class="topbar">
    <div class="brand"><span class="dot"></span>AI COACH · OBSERVABILITY</div>
    <div class="meta" id="meta">loading…</div>
  </div>

  <div class="container">
    <div class="grid">
      <div class="card col-3">
        <h3>Total events</h3>
        <div class="stat" id="stat-events">—</div>
        <div class="stat-sub" id="stat-events-sub">last 7 days</div>
      </div>
      <div class="card col-3">
        <h3>LLM calls</h3>
        <div class="stat" id="stat-llm">—</div>
        <div class="stat-sub" id="stat-llm-sub">across all jobs</div>
      </div>
      <div class="card col-3">
        <h3>Intervals success</h3>
        <div class="stat" id="stat-intervals">—</div>
        <div class="stat-sub" id="stat-intervals-sub">api requests</div>
      </div>
      <div class="card col-3">
        <h3>Emails sent</h3>
        <div class="stat" id="stat-emails">—</div>
        <div class="stat-sub" id="stat-emails-sub">last sent —</div>
      </div>

      <div class="card col-6">
        <h3>Messages per day</h3>
        <div class="chart-wrap"><canvas id="chart-msgs"></canvas></div>
      </div>
      <div class="card col-6">
        <h3>Tokens per day</h3>
        <div class="chart-wrap"><canvas id="chart-tokens"></canvas></div>
      </div>

      <div class="card col-6">
        <h3>LLM calls by job</h3>
        <div class="chart-wrap"><canvas id="chart-jobs"></canvas></div>
      </div>
      <div class="card col-6">
        <h3>Avg latency by job (ms)</h3>
        <div class="chart-wrap"><canvas id="chart-latency"></canvas></div>
      </div>

      <div class="card col-12">
        <h3>Recent errors</h3>
        <div id="errors-table"></div>
      </div>

      <div class="card col-12">
        <h3>Recent events</h3>
        <div id="events-table"></div>
      </div>
    </div>

    <div class="footer">
      <span id="footer-meta">data: docs/logs.json</span>
    </div>
  </div>

<script>
const CSS = getComputedStyle(document.documentElement);
const COLORS = {
  fg: CSS.getPropertyValue('--fg').trim(),
  dim: CSS.getPropertyValue('--fg-dim').trim(),
  mute: CSS.getPropertyValue('--fg-mute').trim(),
  border: CSS.getPropertyValue('--border').trim(),
  accent: CSS.getPropertyValue('--accent').trim(),
  accent2: CSS.getPropertyValue('--accent-2').trim(),
  warn: CSS.getPropertyValue('--warn').trim(),
  err: CSS.getPropertyValue('--err').trim(),
};

Chart.defaults.color = COLORS.dim;
Chart.defaults.borderColor = COLORS.border;
Chart.defaults.font.family = '"JetBrains Mono", monospace';
Chart.defaults.font.size = 11;

function fmt(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}
function timeAgo(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return sec + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  return Math.floor(sec / 86400) + 'd ago';
}
function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderTable(rows, columns, emptyMessage) {
  if (!rows || rows.length === 0) {
    return '<div class="empty">' + emptyMessage + '</div>';
  }
  let html = '<table><thead><tr>';
  for (const c of columns) html += '<th>' + escapeHtml(c.label) + '</th>';
  html += '</tr></thead><tbody>';
  for (const r of rows) {
    html += '<tr>';
    for (const c of columns) html += '<td' + (c.dim ? ' class="dim"' : '') + '>' + (c.render ? c.render(r) : escapeHtml(r[c.key] ?? '')) + '</td>';
    html += '</tr>';
  }
  return html + '</tbody></table>';
}

function lineChart(ctx, labels, data, colour) {
  return new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data,
        borderColor: colour,
        backgroundColor: colour + '22',
        borderWidth: 1.5,
        tension: 0.25,
        fill: true,
        pointRadius: 2,
        pointBackgroundColor: colour,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: COLORS.border, drawTicks: false }, ticks: { color: COLORS.dim } },
        y: { grid: { color: COLORS.border, drawTicks: false }, ticks: { color: COLORS.dim }, beginAtZero: true },
      },
    },
  });
}

function barChart(ctx, labels, data, colour) {
  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{ data, backgroundColor: colour, borderRadius: 2 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: COLORS.dim } },
        y: { grid: { color: COLORS.border, drawTicks: false }, ticks: { color: COLORS.dim }, beginAtZero: true },
      },
    },
  });
}

async function load() {
  const res = await fetch('logs.json?t=' + Date.now(), { cache: 'no-store' });
  const m = await res.json();

  document.getElementById('meta').innerHTML =
    'window <strong>' + m.window_days + 'd</strong> · ' +
    'last event <strong>' + timeAgo(m.last_event_timestamp) + '</strong> · ' +
    'built <strong>' + new Date(m.generated_at).toLocaleString() + '</strong>';

  document.getElementById('stat-events').textContent = fmt(m.total_events);

  const llmTotal = Object.values(m.llm_calls_by_job || {}).reduce((a,b)=>a+b,0);
  document.getElementById('stat-llm').textContent = fmt(llmTotal);

  if (m.intervals_success_rate === null || m.intervals_success_rate === undefined) {
    document.getElementById('stat-intervals').innerHTML = '—';
    document.getElementById('stat-intervals-sub').textContent = 'no api requests yet';
  } else {
    document.getElementById('stat-intervals').innerHTML = m.intervals_success_rate + '<span class="unit">%</span>';
    document.getElementById('stat-intervals-sub').textContent = m.intervals_total + ' calls · ' + m.intervals_failed + ' failed';
  }

  document.getElementById('stat-emails').textContent = fmt(m.email_count);
  document.getElementById('stat-emails-sub').textContent = m.last_email_at ? ('last sent ' + timeAgo(m.last_email_at)) : 'last sent —';

  const msgLabels = Object.keys(m.messages_per_day || {});
  const msgData = Object.values(m.messages_per_day || {});
  if (msgLabels.length) lineChart(document.getElementById('chart-msgs').getContext('2d'), msgLabels, msgData, COLORS.accent);

  const tokLabels = Object.keys(m.tokens_per_day || {});
  const tokData = Object.values(m.tokens_per_day || {});
  if (tokLabels.length) lineChart(document.getElementById('chart-tokens').getContext('2d'), tokLabels, tokData, COLORS.accent2);

  const jobLabels = Object.keys(m.llm_calls_by_job || {});
  const jobData = Object.values(m.llm_calls_by_job || {});
  if (jobLabels.length) barChart(document.getElementById('chart-jobs').getContext('2d'), jobLabels, jobData, COLORS.accent);

  const latLabels = Object.keys(m.avg_latency_by_job || {});
  const latData = Object.values(m.avg_latency_by_job || {});
  if (latLabels.length) barChart(document.getElementById('chart-latency').getContext('2d'), latLabels, latData, COLORS.warn);

  document.getElementById('errors-table').innerHTML = renderTable(
    m.recent_errors || [],
    [
      { label: 'When', render: r => '<span class="dim">' + timeAgo(r.timestamp) + '</span>' },
      { label: 'Type', render: r => '<span class="pill sev-' + (r.severity || 'info') + '">' + escapeHtml(r.event_type) + '</span>' },
      { label: 'Job', key: 'job', dim: true },
      { label: 'Message', render: r => escapeHtml(r.message) },
    ],
    'No errors. Nice.'
  );

  document.getElementById('events-table').innerHTML = renderTable(
    m.recent_events || [],
    [
      { label: 'When', render: r => '<span class="dim">' + timeAgo(r.timestamp) + '</span>' },
      { label: 'Type', render: r => '<span class="pill sev-' + (r.severity || 'info') + '">' + escapeHtml(r.event_type) + '</span>' },
      { label: 'Job', key: 'job', dim: true },
      { label: 'Model', key: 'model_used', dim: true },
      { label: 'Latency', render: r => r.latency_ms ? (r.latency_ms + 'ms') : '<span class="dim">—</span>' },
      { label: 'Tokens', render: r => (r.tokens_in || r.tokens_out) ? ((r.tokens_in||0) + '/' + (r.tokens_out||0)) : '<span class="dim">—</span>' },
      { label: 'Message', render: r => escapeHtml(r.message) },
    ],
    'No events yet.'
  );

  document.getElementById('footer-meta').textContent = 'window ' + m.window_days + ' days · ' + (m.total_events) + ' events · generated ' + m.generated_at;
}

load().catch(err => {
  document.getElementById('meta').textContent = 'failed to load logs.json: ' + err.message;
});
</script>
</body>
</html>
"""


async def build_dashboard(window_days: int = 7) -> dict[str, Path]:
    """
    Build `docs/index.html` and `docs/logs.json` from the current event log.

    Returns the paths written.
    """
    metrics = await fetch_dashboard_metrics(window_days=window_days)

    docs_dir: Path = settings.DOCS_DIR
    docs_dir.mkdir(parents=True, exist_ok=True)

    logs_path = docs_dir / "logs.json"
    html_path = docs_dir / "index.html"

    logs_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    html_path.write_text(HTML_TEMPLATE, encoding="utf-8")

    logger.info("Dashboard built: %s (%d events)", html_path, metrics["total_events"])

    return {"html": html_path, "json": logs_path}


def metrics_summary_text(metrics: dict[str, Any]) -> str:
    """One-line human-readable summary of metrics — useful for logs / Telegram."""
    return (
        f"events={metrics.get('total_events', 0)} "
        f"intervals_ok={metrics.get('intervals_success_rate')}% "
        f"emails={metrics.get('email_count', 0)} "
        f"errors={len(metrics.get('recent_errors', []))}"
    )
