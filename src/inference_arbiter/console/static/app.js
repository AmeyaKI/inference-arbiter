/* inference-arbiter console */

const MAX_ROWS = 120;
const COLD_START_OBS = 500;

const liveFeed = document.getElementById("live-feed");
const feedEmpty = document.getElementById("feed-empty");

let benchChart = null;
let benchPollTimer = null;
let metricsPollTimer = null;
let benchStartTime = null;
let benchDuration = 0;
let benchUsers = 0;
let benchSpawnRate = 0;
const charts = {};

// ── helpers ──────────────────────────────────────────────────

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function fmtMs(ms) {
  if (ms == null || ms === undefined) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function latCls(ms) {
  if (!ms) return "";
  if (ms < 500)  return "fast";
  if (ms < 2000) return "mid";
  return "slow";
}

function tierCls(tier) {
  const t = (tier || "").toLowerCase();
  if (t === "small")  return "tier-small";
  if (t === "medium") return "tier-medium";
  if (t === "large")  return "tier-large";
  return "tier-unknown";
}

function pctDelta(a, b) {
  if (!b || !a) return null;
  return Math.round(((b - a) / b) * 100);
}

// ── tabs ─────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "metrics") startMetricsPoll();
    else stopMetricsPoll();
  });
});

// ── sliders ───────────────────────────────────────────────────

["users", "ramp"].forEach((key) => {
  const el = document.getElementById(`bench-${key}`);
  const lbl = document.getElementById(`val-${key}`);
  if (el && lbl) el.addEventListener("input", () => { lbl.textContent = el.value; });
});

const durEl = document.getElementById("bench-duration");
const durLbl = document.getElementById("val-duration");
if (durEl) durEl.addEventListener("input", () => { durLbl.textContent = `${durEl.value}s`; });

// ── scenario hints ────────────────────────────────────────────

const HINTS = {
  baseline:    "Routes every request to the large (8b) model — no routing intelligence. Use as the cost and latency ceiling.",
  arbiter:     "LinUCB bandit selects tier based on prompt complexity + SLO budget. The system under test.",
  round_robin: "Cycles evenly across small / medium / large. Simulates naive load distribution.",
};

const scenarioSel = document.getElementById("bench-scenario");
const scenarioHint = document.getElementById("scenario-hint");

function refreshHint() {
  if (scenarioHint) scenarioHint.textContent = HINTS[scenarioSel.value] || "";
}
scenarioSel.addEventListener("change", refreshHint);
refreshHint();

// ── health poll ───────────────────────────────────────────────

async function pollHealth() {
  const dotGW  = document.getElementById("dot-gateway");
  const lblGW  = document.getElementById("lbl-gateway");
  const dotBD  = document.getElementById("dot-bandit");
  const lblBD  = document.getElementById("lbl-bandit");

  try {
    const data = await fetch("/console/api/health").then((r) => r.json());

    dotGW.className = "status-dot ok";
    lblGW.textContent = "gateway active";

    if (data.bandit_policy_active) {
      dotBD.className = "status-dot ok";
      lblBD.textContent = "bandit learned";
    } else {
      dotBD.className = "status-dot warn";
      lblBD.textContent = "bandit heuristic";
    }
  } catch {
    dotGW.className = "status-dot err";
    lblGW.textContent = "gateway offline";
    dotBD.className = "status-dot";
    lblBD.textContent = "—";
  }
}

setInterval(pollHealth, 5000);
pollHealth();

// ── SSE live feed ─────────────────────────────────────────────

const sseBadge = document.getElementById("sse-status");

function setSseConnected(ok) {
  if (sseBadge) sseBadge.classList.toggle("hidden", ok);
}

function connectSSE() {
  const src = new EventSource("/console/api/events");

  src.onmessage = (ev) => {
    setSseConnected(true);
    try {
      addFeedRow(JSON.parse(ev.data));
    } catch (e) {
      console.error("SSE parse error", e);
    }
  };

  src.onerror = () => {
    setSseConnected(false);
    src.close();
    setTimeout(connectSSE, 3000);
  };
}

function addFeedRow(event) {
  if (feedEmpty) feedEmpty.classList.add("hidden");

  const tier = (event.final_tier || "unknown").toLowerCase();
  const cascade =
    event.tiers_attempted && event.tiers_attempted.length > 1
      ? event.tiers_attempted.join(" → ")
      : tier;
  const reason = event.routing_reason ? ` · ${event.routing_reason}` : "";

  const row = document.createElement("div");
  row.className = "feed-row entering";
  row.innerHTML = `
    <div class="tier-cell ${tierCls(tier)}">
      <span class="tier-pip"></span>
      <span class="tier-label-text">${esc(tier)}</span>
    </div>
    <div class="feed-prompt">${esc(event.prompt_preview || "—")}</div>
    <div class="feed-route">${esc(cascade + reason)}</div>
    <div class="feed-latency ${latCls(event.elapsed_ms)}">${fmtMs(event.elapsed_ms)}</div>
  `;
  row.addEventListener("click", () => showAudit(event.request_id));

  liveFeed.insertBefore(row, feedEmpty ? feedEmpty.nextSibling : liveFeed.firstChild);

  // Trim excess rows (keep feedEmpty in place)
  const rows = liveFeed.querySelectorAll(".feed-row");
  if (rows.length > MAX_ROWS) rows[rows.length - 1].remove();
}

// ── audit modal ───────────────────────────────────────────────

async function showAudit(requestId) {
  const modal   = document.getElementById("audit-modal");
  const pre     = document.getElementById("audit-json");
  const spinner = document.getElementById("audit-spinner");

  pre.textContent = "";
  spinner.classList.remove("hidden");
  modal.classList.remove("hidden");

  try {
    const data = await fetch(`/console/api/routing/${requestId}`).then((r) => r.json());
    pre.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    pre.textContent = String(e);
  } finally {
    spinner.classList.add("hidden");
  }
}

document.getElementById("modal-close").addEventListener("click", () => {
  document.getElementById("audit-modal").classList.add("hidden");
});

document.getElementById("audit-modal").addEventListener("click", (e) => {
  if (e.target === document.getElementById("audit-modal")) {
    document.getElementById("audit-modal").classList.add("hidden");
  }
});

document.getElementById("audit-copy").addEventListener("click", () => {
  const text = document.getElementById("audit-json").textContent;
  if (!text) return;
  navigator.clipboard.writeText(text).catch(() => {});
  const btn = document.getElementById("audit-copy");
  const orig = btn.innerHTML;
  btn.textContent = "Copied!";
  setTimeout(() => { btn.innerHTML = orig; lucide.createIcons({ nodes: [btn] }); }, 1500);
});

// ── benchmark ─────────────────────────────────────────────────

document.getElementById("bench-start").addEventListener("click", startBench);
document.getElementById("bench-stop").addEventListener("click", stopBench);

async function startBench() {
  const scenario  = document.getElementById("bench-scenario").value;
  const users     = parseInt(document.getElementById("bench-users").value, 10);
  const spawnRate = parseFloat(document.getElementById("bench-ramp").value);
  const durationS = parseFloat(document.getElementById("bench-duration").value);

  benchStartTime  = Date.now();
  benchDuration   = durationS;
  benchUsers      = users;
  benchSpawnRate  = spawnRate;

  document.getElementById("bench-start").disabled = true;
  document.getElementById("bench-stop").disabled  = false;
  document.getElementById("stat-running").textContent = "starting";

  const rampWrap = document.getElementById("ramp-wrap");
  rampWrap.classList.remove("hidden");
  document.getElementById("ramp-label").textContent =
    `Spawning ${users} workers at ${spawnRate}/s…`;

  initBenchChart();

  await fetch("/console/api/benchmark/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario, users, spawn_rate: spawnRate, duration_s: durationS }),
  });

  benchPollTimer = setInterval(pollBench, 1000);
}

async function stopBench() {
  await fetch("/console/api/benchmark/stop", { method: "POST" });
  clearInterval(benchPollTimer);
  benchPollTimer = null;
  document.getElementById("bench-start").disabled  = false;
  document.getElementById("bench-stop").disabled   = true;
  document.getElementById("ramp-wrap").classList.add("hidden");
  pollBench();
}

async function pollBench() {
  try {
    const data = await fetch("/console/api/benchmark/status").then((r) => r.json());

    document.getElementById("stat-requests").textContent = data.requests ?? "—";
    document.getElementById("stat-rps").textContent =
      typeof data.rps === "number" ? data.rps.toFixed(1) : "—";
    document.getElementById("stat-failures").textContent = data.failures ?? "—";
    document.getElementById("stat-p50").textContent = fmtMs(data.p50_ms);
    document.getElementById("stat-p95").textContent = fmtMs(data.p95_ms);

    // Ramp-up progress
    if (benchStartTime && data.running) {
      const elapsed = (Date.now() - benchStartTime) / 1000;
      const rampSec = Math.ceil(benchUsers / benchSpawnRate);
      const rampWrap = document.getElementById("ramp-wrap");

      if (elapsed < rampSec) {
        rampWrap.classList.remove("hidden");
        const p = Math.min(elapsed / rampSec, 1);
        document.getElementById("ramp-fill").style.width = `${p * 100}%`;
        document.getElementById("ramp-label").textContent =
          `Ramping up… ${Math.round(p * benchUsers)}/${benchUsers} workers`;
        document.getElementById("stat-running").textContent = "ramping";
      } else {
        rampWrap.classList.add("hidden");
        document.getElementById("stat-running").textContent = "running";
      }
    }

    // Chart
    if (benchChart && data.running) {
      const elapsed = benchStartTime ? Math.round((Date.now() - benchStartTime) / 1000) : 0;
      benchChart.data.labels.push(`${elapsed}s`);
      benchChart.data.datasets[0].data.push(
        typeof data.rps === "number" ? data.rps : 0
      );
      if (benchChart.data.labels.length > 90) {
        benchChart.data.labels.shift();
        benchChart.data.datasets[0].data.shift();
      }
      benchChart.update("none");
    }

    if (!data.running && benchPollTimer) {
      document.getElementById("bench-start").disabled  = false;
      document.getElementById("bench-stop").disabled   = true;
      document.getElementById("ramp-wrap").classList.add("hidden");
      document.getElementById("stat-running").textContent = "idle";
      clearInterval(benchPollTimer);
      benchPollTimer = null;
    }

    renderComparison(data.completed_runs || {});
  } catch (_) {}
}

function initBenchChart() {
  const ctx = document.getElementById("bench-chart");
  if (benchChart) benchChart.destroy();
  benchChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        data: [],
        borderColor: "#0070f3",
        backgroundColor: "rgba(0,112,243,0.08)",
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: {
          beginAtZero: true,
          grid: { color: "#242424" },
          ticks: { color: "#737373", font: { family: "'JetBrains Mono'" } },
          title: { display: true, text: "req/s", color: "#737373", font: { size: 11 } },
        },
      },
    },
  });
}

// ── comparison card ──────────────────────────────────────────

function renderComparison(runs) {
  const baseline = runs.baseline;
  const arbiter  = runs.arbiter;
  const card  = document.getElementById("compare-card");
  const table = document.getElementById("compare-table");
  const badge = document.getElementById("bench-badge");

  if (!baseline && !arbiter) return;

  card.classList.remove("hidden");
  badge.classList.remove("hidden");

  function deltaTag(d, goodWhenPositive = false) {
    if (d == null) return "";
    const good = goodWhenPositive ? d > 0 : d > 0;
    const cls  = good ? "delta-good" : "delta-bad";
    const sign = d > 0 ? "−" : "+";
    return `<span class="delta ${cls}">${sign}${Math.abs(d)}%</span>`;
  }

  if (baseline && arbiter) {
    const p50d = pctDelta(arbiter.p50_ms, baseline.p50_ms);
    const p95d = pctDelta(arbiter.p95_ms, baseline.p95_ms);
    const rpsd = baseline.rps > 0
      ? Math.round(((arbiter.rps - baseline.rps) / baseline.rps) * 100)
      : null;

    table.innerHTML = `
      <div class="compare-col">
        <div class="compare-col-head">Metric</div>
        <div class="compare-row"><div class="compare-row-label">P50 latency</div></div>
        <div class="compare-row"><div class="compare-row-label">P95 latency</div></div>
        <div class="compare-row"><div class="compare-row-label">Throughput</div></div>
      </div>
      <div class="compare-col">
        <div class="compare-col-head">Baseline</div>
        <div class="compare-row"><div class="compare-row-val">${fmtMs(baseline.p50_ms)}</div></div>
        <div class="compare-row"><div class="compare-row-val">${fmtMs(baseline.p95_ms)}</div></div>
        <div class="compare-row"><div class="compare-row-val">${baseline.rps} rps</div></div>
      </div>
      <div class="compare-col">
        <div class="compare-col-head">Arbiter</div>
        <div class="compare-row"><div class="compare-row-val">${fmtMs(arbiter.p50_ms)} ${deltaTag(p50d)}</div></div>
        <div class="compare-row"><div class="compare-row-val">${fmtMs(arbiter.p95_ms)} ${deltaTag(p95d)}</div></div>
        <div class="compare-row"><div class="compare-row-val">${arbiter.rps} rps ${rpsd != null ? deltaTag(-rpsd, true) : ""}</div></div>
      </div>
    `;
  } else {
    const run  = baseline || arbiter;
    const name = baseline ? "Baseline" : "Arbiter";
    const next = baseline ? "Run Arbiter to compare" : "Run Baseline to compare";
    table.innerHTML = `
      <div class="compare-col" style="grid-column:1/-1">
        <div class="compare-col-head">${name} complete</div>
        <div class="compare-row">
          <div class="compare-row-val">P50 ${fmtMs(run.p50_ms)} · P95 ${fmtMs(run.p95_ms)} · ${run.rps} rps</div>
        </div>
        <div class="sub-text" style="margin-top:6px">${next}</div>
      </div>
    `;
  }
}

// ── metrics tab ──────────────────────────────────────────────

function startMetricsPoll() {
  pollMetrics();
  metricsPollTimer = setInterval(pollMetrics, 5000);
}

function stopMetricsPoll() {
  clearInterval(metricsPollTimer);
  metricsPollTimer = null;
}

async function pollMetrics() {
  const banner = document.getElementById("metrics-banner");
  try {
    const data = await fetch("/console/api/metrics/summary").then((r) => r.json());

    if (!data.available) {
      banner.textContent = `Prometheus unavailable: ${data.error || "not reachable"} — start with: docker compose up -d`;
      banner.classList.remove("hidden");
      return;
    }
    banner.classList.add("hidden");

    renderBar("chart-tier-rate", data.tier_rate,      "req/s");
    renderPie("chart-reasons",   data.routing_reasons);
    renderBar("chart-cost",      data.cost_rate,       "cost/s");

    // SLO
    const sloEl = document.getElementById("metric-slo");
    if (data.slo_attainment_rate != null) {
      const v = (data.slo_attainment_rate * 100).toFixed(1);
      sloEl.textContent = `${v}%`;
      sloEl.style.color = data.slo_attainment_rate >= 0.95 ? "var(--success)"
        : data.slo_attainment_rate >= 0.8 ? "var(--warning)" : "var(--danger)";
    } else {
      sloEl.textContent = "—";
      sloEl.style.color = "";
    }
    document.getElementById("metric-slo-detail").textContent =
      `${data.slo_met || 0} met / ${data.slo_evaluated || 0} evaluated`;

    // Bandit
    const active = data.bandit_policy_active;
    const bdEl = document.getElementById("metric-bandit");
    bdEl.textContent = active ? "Active" : "Heuristic";
    bdEl.style.color = active ? "var(--success)" : "var(--warning)";

    const obs = data.bandit_observations || {};
    const obsEl = document.getElementById("metric-bandit-obs");
    if (Object.keys(obs).length === 0) {
      obsEl.innerHTML = '<span class="sub-text">No observations yet</span>';
    } else {
      obsEl.innerHTML = Object.entries(obs).map(([tier, count]) => {
        const n   = Math.round(count);
        const pct = Math.min((n / COLD_START_OBS) * 100, 100);
        const done = n >= COLD_START_OBS;
        return `
          <div class="bandit-row">
            <span class="bandit-tier-name">${tier}</span>
            <div class="bandit-track">
              <div class="bandit-fill ${done ? "done" : ""}" style="width:${pct}%"></div>
            </div>
            <span class="bandit-count">${n}/${COLD_START_OBS}</span>
          </div>`;
      }).join("");
    }

    // Endpoint health
    const epEl    = document.getElementById("metric-endpoints");
    const inFlight = data.endpoint_in_flight || {};
    const circuits = data.circuit_breaker_state || {};
    if (Object.keys(inFlight).length === 0) {
      epEl.innerHTML = '<span class="sub-text">No endpoint data</span>';
    } else {
      epEl.innerHTML = Object.keys(inFlight).map((name) => {
        const cb  = circuits[name];
        const cbLabel = cb === 0 ? "closed" : cb === 1 ? "half-open" : "open";
        const cbColor = cb === 0 ? "var(--success)" : cb === 1 ? "var(--warning)" : "var(--danger)";
        return `
          <div class="ep-row">
            <span class="ep-name">${name}</span>
            <span class="ep-meta">
              <span>${Math.round(inFlight[name])} in-flight</span>
              <span style="color:${cbColor}">${cbLabel}</span>
            </span>
          </div>`;
      }).join("");
    }
  } catch (e) {
    banner.textContent = String(e);
    banner.classList.remove("hidden");
  }
}

function renderBar(id, series, yLabel) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  const labels = Object.keys(series);
  const values = Object.values(series);
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: ["#22c55e", "#f59e0b", "#ef4444", "#0070f3"],
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: "#242424" },
          ticks: { color: "#737373", font: { family: "'JetBrains Mono'", size: 10 } },
          title: { display: true, text: yLabel, color: "#737373", font: { size: 10 } },
        },
        x: { ticks: { color: "#737373", font: { family: "'JetBrains Mono'", size: 10 } }, grid: { display: false } },
      },
    },
  });
}

function renderPie(id, series) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: Object.keys(series),
      datasets: [{
        data: Object.values(series),
        backgroundColor: ["#0070f3", "#22c55e", "#f59e0b", "#ef4444", "#a855f7"],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      cutout: "62%",
      plugins: {
        legend: {
          position: "right",
          labels: { color: "#737373", font: { size: 11, family: "'JetBrains Mono'" }, boxWidth: 10 },
        },
      },
    },
  });
}

// ── init ──────────────────────────────────────────────────────

connectSSE();
lucide.createIcons();
