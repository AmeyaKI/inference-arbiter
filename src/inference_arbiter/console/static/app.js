/* inference-arbiter console */

const MAX_ROWS = 120;
const COLD_START_OBS = 500;

const CHART_THEME = {
  grid: "#2e2c28",
  ticks: "#b0aea5",
  font: "'JetBrains Mono'",
  accent: "#d97757",
  accentFill: "rgba(217, 119, 87, 0.12)",
  colors: {
    olive: "#788c5d",
    clay: "#d97757",
    fig: "#c46686",
    sky: "#6a9bcc",
    cactus: "#bcd1ca",
  },
  barColors: ["#788c5d", "#d97757", "#c46686", "#6a9bcc"],
  pieColors: ["#d97757", "#788c5d", "#c46686", "#6a9bcc", "#bcd1ca"],
};

const liveFeed = document.getElementById("live-feed");
const feedEmpty = document.getElementById("feed-empty");

let benchChart = null;
let benchPollTimer = null;
let metricsPollTimer = null;
let benchStartTime = null;
let benchDuration = 0;
let benchUsers = 0;
let benchSpawnRate = 0;
let lastAuditData = null;
let activeAuditTab = "summary";
const charts = {};

// ── helpers ──────────────────────────────────────────────────

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

function fmtMs(ms) {
  if (ms == null || ms === undefined) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function latCls(ms) {
  if (!ms) return "";
  if (ms < 500) return "fast";
  if (ms < 2000) return "mid";
  return "slow";
}

function tierCls(tier) {
  const t = (tier || "").toLowerCase();
  if (t === "small") return "tier-small";
  if (t === "medium") return "tier-medium";
  if (t === "large") return "tier-large";
  return "tier-unknown";
}

function tierPill(tier) {
  const t = (tier || "unknown").toLowerCase();
  const cls = t === "small" ? "pill-small" : t === "medium" ? "pill-medium" : t === "large" ? "pill-large" : "pill-unknown";
  return `<span class="pill ${cls}">${esc(t)}</span>`;
}

function pctDelta(a, b) {
  if (!b || !a) return null;
  return Math.round(((b - a) / b) * 100);
}

function showToast(msg) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
}

function chartScales(yLabel) {
  return {
    x: {
      ticks: { color: CHART_THEME.ticks, font: { family: CHART_THEME.font, size: 10 } },
      grid: { display: false },
    },
    y: {
      beginAtZero: true,
      grid: { color: CHART_THEME.grid },
      ticks: { color: CHART_THEME.ticks, font: { family: CHART_THEME.font, size: 10 } },
      title: yLabel
        ? { display: true, text: yLabel, color: CHART_THEME.ticks, font: { size: 10 } }
        : undefined,
    },
  };
}

function timeAgo(epochMs) {
  if (!epochMs) return "—";
  const sec = Math.max(0, Math.round((Date.now() - epochMs) / 1000));
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m`;
}

// ── tabs ─────────────────────────────────────────────────────

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((t) => {
    const active = t.dataset.tab === tabName;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  const panel = document.getElementById(`panel-${tabName}`);
  if (panel) panel.classList.add("active");
  if (tabName === "metrics") startMetricsPoll();
  else stopMetricsPoll();
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

document.querySelectorAll(".goto-tab").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    switchTab(btn.dataset.goto);
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

// ── scenario cards ───────────────────────────────────────────

const HINTS = {
  baseline: "All requests go to one fixed model tier. Useful as a cost and latency ceiling.",
  arbiter: "LinUCB bandit selects tier based on prompt complexity + SLO budget. The system under test.",
  round_robin: "Cycles evenly across small / medium / large. Simulates naive load distribution.",
  random: "Each request goes to a uniformly random tier. Good baseline for comparing against intelligent routing.",
};

const scenarioSel = document.getElementById("bench-scenario");
const scenarioHint = document.getElementById("scenario-hint");
const baselineModelRow = document.getElementById("baseline-model-row");

function selectScenario(value) {
  if (scenarioSel) scenarioSel.value = value;
  document.querySelectorAll(".scenario-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.scenario === value);
  });
  refreshHint();
}

function refreshHint() {
  const val = scenarioSel?.value || "arbiter";
  if (scenarioHint) scenarioHint.textContent = HINTS[val] || "";
  if (baselineModelRow) {
    baselineModelRow.style.display = val === "baseline" ? "" : "none";
  }
}

document.querySelectorAll(".scenario-card").forEach((card) => {
  card.addEventListener("click", () => selectScenario(card.dataset.scenario));
});

if (scenarioSel) scenarioSel.addEventListener("change", refreshHint);
refreshHint();

// ── health poll ───────────────────────────────────────────────

async function pollHealth() {
  const dotGW = document.getElementById("dot-gateway");
  const lblGW = document.getElementById("lbl-gateway");
  const dotBD = document.getElementById("dot-bandit");
  const lblBD = document.getElementById("lbl-bandit");

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
  const ts = event.timestamp ? Math.round(event.timestamp * 1000) : Date.now();

  const row = document.createElement("div");
  row.className = "feed-row entering";
  row.innerHTML = `
    <div class="tier-cell">${tierPill(tier)}</div>
    <div class="feed-prompt">${esc(event.prompt_preview || "—")}</div>
    <div class="feed-route">${esc(cascade + reason)}</div>
    <div class="feed-latency ${latCls(event.elapsed_ms)}">${fmtMs(event.elapsed_ms)}</div>
    <div class="feed-row-audit">audit →</div>
  `;
  row.addEventListener("click", () => showAudit(event.request_id));

  liveFeed.insertBefore(row, feedEmpty ? feedEmpty.nextSibling : liveFeed.firstChild);

  const rows = liveFeed.querySelectorAll(".feed-row");
  if (rows.length > MAX_ROWS) rows[rows.length - 1].remove();
}

// ── audit modal ───────────────────────────────────────────────

function setAuditTab(tab) {
  activeAuditTab = tab;
  document.querySelectorAll(".audit-tab").forEach((t) => {
    const active = t.dataset.auditTab === tab;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.getElementById("audit-panel-summary").classList.toggle("active", tab === "summary");
  document.getElementById("audit-panel-raw").classList.toggle("active", tab === "raw");
}

document.querySelectorAll(".audit-tab").forEach((tab) => {
  tab.addEventListener("click", () => setAuditTab(tab.dataset.auditTab));
});

function priorityBadge(priority) {
  const p = (priority || "standard").toLowerCase();
  return `<span class="priority-badge priority-${p}">${esc(p)}</span>`;
}

function renderAuditSummary(data) {
  const el = document.getElementById("audit-summary");
  if (!el) return;

  const history = data.routing_history || [];
  const metrics = data.metrics || {};
  const payload = data.payload || {};
  const totalBudget = metrics.total_slo_budget_ms;
  const elapsed = metrics.current_elapsed_ms || 0;
  const remaining = metrics.remaining_ms;

  let sloHtml = "";
  if (totalBudget != null) {
    const pct = Math.min(100, Math.round((elapsed / totalBudget) * 100));
    sloHtml = `
      <div class="audit-section-title">SLO budget</div>
      <div class="slo-bar-wrap">
        <div class="slo-bar-meta">
          <span>elapsed: <strong>${fmtMs(elapsed)}</strong></span>
          <span>budget: <strong>${fmtMs(totalBudget)}</strong></span>
          <span>remaining: <strong>${remaining != null ? fmtMs(remaining) : "—"}</strong></span>
        </div>
        <div class="slo-bar-track">
          <div class="slo-bar-fill" style="width:${pct}%"></div>
        </div>
      </div>`;
  } else {
    sloHtml = `<div class="sub-text">No SLO deadline set for this request.</div>`;
  }

  const steps = history.map((step, i) => {
    const isLast = i === history.length - 1;
    const passed = step.verification_status === "PASSED";
    const stepCls = isLast && data.status === "completed" ? "final" : passed ? "pass" : "fail";
    const icon = isLast && data.status === "completed" ? "✓" : passed ? "✓" : "×";
    return `
      <div class="cascade-step ${stepCls}">
        <div class="cascade-line"><div class="cascade-dot">${icon}</div></div>
        <div class="cascade-body">
          <div class="cascade-tier">${tierPill(step.tier)} <span style="color:var(--text-4);font-weight:400">${esc(step.backend_model || "")}</span></div>
          <div class="cascade-meta">
            <span>verify: ${esc(step.verification_status)}</span>
            <span>latency: ${fmtMs(step.latency_ms)}</span>
            ${step.ttft_ms != null ? `<span>ttft: ${fmtMs(step.ttft_ms)}</span>` : ""}
            <span>failure: ${esc(step.failure_attribution || "none")}</span>
            ${step.budget_remaining_ms != null ? `<span>budget left: ${fmtMs(step.budget_remaining_ms)}</span>` : ""}
          </div>
        </div>
      </div>`;
  }).join("");

  let banditHtml = "";
  if (data.bandit_scores && Object.keys(data.bandit_scores).length > 0) {
    const maxScore = Math.max(...Object.values(data.bandit_scores), 0.001);
    const rows = Object.entries(data.bandit_scores)
      .sort((a, b) => b[1] - a[1])
      .map(([tier, score]) => {
        const pct = Math.round((score / maxScore) * 100);
        return `
          <div class="bandit-score-row">
            <span class="bandit-score-tier">${esc(tier)}</span>
            <div class="bandit-score-track">
              <div class="bandit-score-fill" style="width:${pct}%"></div>
            </div>
            <span class="bandit-score-val">${score.toFixed(3)}</span>
          </div>`;
      }).join("");
    banditHtml = `
      <div class="audit-section-title">Bandit scores</div>
      <div class="bandit-scores">${rows}</div>`;
  }

  el.innerHTML = `
    <div class="audit-header-row">
      <span class="audit-request-id">${esc(data.request_id)}</span>
      ${priorityBadge(data.priority)}
      ${data.final_tier ? tierPill(data.final_tier) : ""}
      ${data.routing_reason ? `<span class="chip">reason: <strong>${esc(data.routing_reason)}</strong></span>` : ""}
    </div>
    ${sloHtml}
    <div class="audit-section-title" style="margin-top:8px">Cascade timeline</div>
    <div class="cascade-timeline">${steps || '<span class="sub-text">No tier attempts recorded.</span>'}</div>
    ${banditHtml}
    <div class="audit-section-title" style="margin-top:8px">Prompt metadata</div>
    <div class="audit-meta-grid">
      <span>tokens: <strong>${payload.estimated_tokens ?? "—"}</strong></span>
      <span>hash: <strong>${esc(payload.prompt_hash || "—")}</strong></span>
      <span>model requested: <strong>${esc(data.requested_model || "auto")}</strong></span>
      <span>status: <strong>${esc(data.status || "—")}</strong></span>
    </div>
  `;
}

async function showAudit(requestId) {
  const modal = document.getElementById("audit-modal");
  const pre = document.getElementById("audit-json");
  const spinner = document.getElementById("audit-spinner");

  pre.textContent = "";
  document.getElementById("audit-summary").innerHTML = "";
  lastAuditData = null;
  spinner.classList.remove("hidden");
  modal.classList.remove("hidden");
  setAuditTab("summary");

  try {
    const data = await fetch(`/console/api/routing/${requestId}`).then((r) => r.json());
    lastAuditData = data;
    pre.textContent = JSON.stringify(data, null, 2);
    renderAuditSummary(data);
  } catch (e) {
    pre.textContent = String(e);
    document.getElementById("audit-summary").innerHTML = `<span class="sub-text" style="color:var(--fig)">${esc(String(e))}</span>`;
  } finally {
    spinner.classList.add("hidden");
  }
}

function closeModal() {
  document.getElementById("audit-modal").classList.add("hidden");
}

document.getElementById("modal-close").addEventListener("click", closeModal);

document.getElementById("audit-modal").addEventListener("click", (e) => {
  if (e.target === document.getElementById("audit-modal")) closeModal();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.getElementById("audit-modal").classList.contains("hidden")) {
    closeModal();
  }
});

document.getElementById("audit-copy").addEventListener("click", () => {
  let text = "";
  if (activeAuditTab === "raw") {
    text = document.getElementById("audit-json").textContent;
  } else if (lastAuditData) {
    text = JSON.stringify(lastAuditData, null, 2);
  }
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => showToast("Copied to clipboard")).catch(() => {});
});

// ── benchmark ─────────────────────────────────────────────────

document.getElementById("bench-start").addEventListener("click", startBench);
document.getElementById("bench-stop").addEventListener("click", stopBench);

async function startBench() {
  const scenario = document.getElementById("bench-scenario").value;
  const users = parseInt(document.getElementById("bench-users").value, 10);
  const spawnRate = parseFloat(document.getElementById("bench-ramp").value);
  const durationS = parseFloat(document.getElementById("bench-duration").value);
  const baselineModel = document.getElementById("baseline-model")?.value || "large";

  benchStartTime = Date.now();
  benchDuration = durationS;
  benchUsers = users;
  benchSpawnRate = spawnRate;

  document.getElementById("bench-start").disabled = true;
  document.getElementById("bench-stop").disabled = false;
  document.getElementById("bench-btn-row").classList.add("btn-running");
  document.getElementById("stat-elapsed").textContent = "0:00";

  const rampWrap = document.getElementById("ramp-wrap");
  rampWrap.classList.remove("hidden");
  document.getElementById("ramp-label").textContent = `Spawning ${users} workers at ${spawnRate}/s…`;

  initBenchChart();

  await fetch("/console/api/benchmark/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scenario,
      users,
      spawn_rate: spawnRate,
      duration_s: durationS,
      baseline_model: baselineModel,
    }),
  });

  benchPollTimer = setInterval(pollBench, 1000);
}

async function stopBench() {
  await fetch("/console/api/benchmark/stop", { method: "POST" });
  clearInterval(benchPollTimer);
  benchPollTimer = null;
  benchStartTime = null;
  document.getElementById("bench-start").disabled = false;
  document.getElementById("bench-stop").disabled = true;
  document.getElementById("bench-btn-row").classList.remove("btn-running");
  document.getElementById("ramp-wrap").classList.add("hidden");
  document.getElementById("stat-elapsed").textContent = "—";
  pollBench();
}

function setStatLatency(id, ms) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = fmtMs(ms);
  el.className = `stat-value ${latCls(ms)}`;
}

async function pollBench() {
  try {
    const data = await fetch("/console/api/benchmark/status").then((r) => r.json());

    document.getElementById("stat-requests").textContent = data.requests ?? "—";
    document.getElementById("stat-rps").textContent =
      typeof data.rps === "number" ? data.rps.toFixed(1) : "—";
    document.getElementById("stat-failures").textContent = data.failures ?? "—";
    setStatLatency("stat-p50", data.p50_ms);
    setStatLatency("stat-p95", data.p95_ms);

    if (benchStartTime && data.running) {
      const elapsed = (Date.now() - benchStartTime) / 1000;
      const mins = Math.floor(elapsed / 60);
      const secs = Math.floor(elapsed % 60);
      document.getElementById("stat-elapsed").textContent =
        `${mins}:${String(secs).padStart(2, "0")}`;

      const rampSec = Math.ceil(benchUsers / benchSpawnRate);
      const rampWrap = document.getElementById("ramp-wrap");
      if (elapsed < rampSec) {
        rampWrap.classList.remove("hidden");
        const p = Math.min(elapsed / rampSec, 1);
        document.getElementById("ramp-fill").style.width = `${p * 100}%`;
        document.getElementById("ramp-label").textContent =
          `Ramping up… ${Math.round(p * benchUsers)}/${benchUsers} workers`;
      } else {
        rampWrap.classList.add("hidden");
      }
    }

    if (benchChart && data.running) {
      const elapsed = benchStartTime ? Math.round((Date.now() - benchStartTime) / 1000) : 0;
      benchChart.data.labels.push(`${elapsed}s`);
      benchChart.data.datasets[0].data.push(typeof data.rps === "number" ? data.rps : 0);
      if (benchChart.data.labels.length > 90) {
        benchChart.data.labels.shift();
        benchChart.data.datasets[0].data.shift();
      }
      benchChart.update("none");
    }

    if (!data.running && benchPollTimer) {
      document.getElementById("bench-start").disabled = false;
      document.getElementById("bench-stop").disabled = true;
      document.getElementById("bench-btn-row").classList.remove("btn-running");
      document.getElementById("ramp-wrap").classList.add("hidden");
      clearInterval(benchPollTimer);
      benchPollTimer = null;
      benchStartTime = null;
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
        borderColor: CHART_THEME.accent,
        backgroundColor: CHART_THEME.accentFill,
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: chartScales("req/s").y,
      },
    },
  });
}

// ── comparison card ──────────────────────────────────────────

function compareBar(pct, good) {
  const color = good ? CHART_THEME.colors.olive : CHART_THEME.colors.fig;
  const w = Math.min(Math.abs(pct || 0), 100);
  return `<div class="compare-bar-wrap"><div class="compare-bar" style="width:${w}%;background:${color}"></div></div>`;
}

function renderComparison(runs) {
  const baseline = runs.baseline;
  const arbiter = runs.arbiter;
  const card = document.getElementById("compare-card");
  const table = document.getElementById("compare-table");
  const badge = document.getElementById("bench-badge");

  if (!baseline && !arbiter) return;

  card.classList.remove("hidden");
  badge.classList.remove("hidden");

  function deltaTag(d, goodWhenPositive = false) {
    if (d == null) return "";
    const good = goodWhenPositive ? d > 0 : d > 0;
    const cls = good ? "delta-good" : d === 0 ? "delta-flat" : "delta-bad";
    const sign = d > 0 ? "−" : d < 0 ? "+" : "";
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
        <div class="compare-row">
          <div class="compare-row-val">${fmtMs(arbiter.p50_ms)} ${deltaTag(p50d)}</div>
          ${compareBar(p50d, p50d > 0)}
        </div>
        <div class="compare-row">
          <div class="compare-row-val">${fmtMs(arbiter.p95_ms)} ${deltaTag(p95d)}</div>
          ${compareBar(p95d, p95d > 0)}
        </div>
        <div class="compare-row">
          <div class="compare-row-val">${arbiter.rps} rps ${rpsd != null ? deltaTag(-rpsd, true) : ""}</div>
          ${rpsd != null ? compareBar(-rpsd, rpsd > 0) : ""}
        </div>
      </div>
    `;
  } else {
    const run = baseline || arbiter;
    const name = baseline ? "Baseline" : "Arbiter";
    const next = baseline ? "Run Arbiter to compare" : "Run Baseline to compare";
    table.innerHTML = `
      <div class="compare-col" style="grid-column:1/-1">
        <div class="compare-col-head">${name} complete</div>
        <div class="compare-row">
          <div class="compare-row-val">P50 ${fmtMs(run.p50_ms)} · P95 ${fmtMs(run.p95_ms)} · ${run.rps} rps</div>
        </div>
        <div class="sub-text" style="margin-top:8px">${next}</div>
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

function updateKpiRing(elId, valId, pct, color) {
  const bg = document.getElementById(elId);
  const val = document.getElementById(valId);
  if (bg) {
    bg.style.setProperty("--ring-pct", `${Math.min(100, pct)}%`);
    bg.style.setProperty("--ring-color", color);
  }
  if (val) val.textContent = pct != null ? `${Math.round(pct)}%` : "—";
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

    renderBar("chart-tier-rate", data.tier_rate, "req/s");
    renderPie("chart-reasons", data.routing_reasons);
    renderBar("chart-cost", data.cost_rate, "cost/s");

    const sloRate = data.slo_attainment_rate;
    if (sloRate != null) {
      const v = (sloRate * 100).toFixed(1);
      const pct = sloRate * 100;
      const color = sloRate >= 0.95 ? CHART_THEME.colors.olive : sloRate >= 0.8 ? CHART_THEME.colors.clay : CHART_THEME.colors.fig;
      updateKpiRing("kpi-slo-ring-bg", "kpi-slo-ring-val", pct, color);
      document.getElementById("metric-slo-detail").textContent =
        `${data.slo_met || 0} met / ${data.slo_evaluated || 0} evaluated (${v}%)`;
    } else {
      updateKpiRing("kpi-slo-ring-bg", "kpi-slo-ring-val", 0, CHART_THEME.colors.clay);
      document.getElementById("metric-slo-detail").textContent = "No SLO data yet";
    }

    const active = data.bandit_policy_active;
    const obs = data.bandit_observations || {};
    const obsVals = Object.values(obs);
    const avgObs = obsVals.length ? obsVals.reduce((a, b) => a + b, 0) / obsVals.length : 0;
    const convPct = Math.min(100, (avgObs / COLD_START_OBS) * 100);
    const banditColor = active ? CHART_THEME.colors.olive : CHART_THEME.colors.clay;

    updateKpiRing("kpi-bandit-ring-bg", "kpi-bandit-ring-val", convPct, banditColor);
    document.getElementById("metric-bandit-kpi").textContent =
      active ? "LinUCB learned policy active" : `Heuristic cold start (${Math.round(avgObs)}/${COLD_START_OBS} avg obs)`;

    const bdEl = document.getElementById("metric-bandit");
    bdEl.textContent = active ? "Active" : "Heuristic";
    bdEl.style.color = active ? "var(--olive)" : "var(--clay)";

    const tierRate = data.tier_rate || {};
    const totalRps = Object.values(tierRate).reduce((a, b) => a + b, 0);
    document.getElementById("kpi-total-rps").textContent = totalRps > 0 ? totalRps.toFixed(2) : "—";

    const obsEl = document.getElementById("metric-bandit-obs");
    if (Object.keys(obs).length === 0) {
      obsEl.innerHTML = '<span class="sub-text">No observations yet</span>';
    } else {
      obsEl.innerHTML = Object.entries(obs).map(([tier, count]) => {
        const n = Math.round(count);
        const pct = Math.min((n / COLD_START_OBS) * 100, 100);
        const done = n >= COLD_START_OBS;
        const tCls = tierCls(tier);
        return `
          <div class="bandit-row">
            <span class="bandit-tier-name">${tier}</span>
            <div class="bandit-track">
              <div class="bandit-fill ${tCls} ${done ? "done" : ""}" style="width:${pct}%"></div>
            </div>
            <span class="bandit-count">${n}/${COLD_START_OBS}${done ? " ✓" : ""}</span>
          </div>`;
      }).join("");
    }

    const epEl = document.getElementById("metric-endpoints");
    const inFlight = data.endpoint_in_flight || {};
    const circuits = data.circuit_breaker_state || {};
    if (Object.keys(inFlight).length === 0) {
      epEl.innerHTML = '<span class="sub-text">No endpoint data</span>';
    } else {
      epEl.innerHTML = Object.keys(inFlight).map((name) => {
        const cb = circuits[name];
        const cbLabel = cb === 0 ? "closed" : cb === 1 ? "half-open" : "open";
        const cbCls = cb === 0 ? "closed" : cb === 1 ? "half-open" : "open";
        return `
          <div class="ep-row">
            <span class="ep-name">${name}</span>
            <span class="ep-meta">
              <span>${Math.round(inFlight[name])} in-flight</span>
              <span class="ep-chip ${cbCls}">${cbLabel}</span>
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
        backgroundColor: CHART_THEME.barColors,
        borderRadius: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      scales: chartScales(yLabel),
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
        backgroundColor: CHART_THEME.pieColors,
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      cutout: "62%",
      plugins: {
        legend: {
          position: "right",
          labels: {
            color: CHART_THEME.ticks,
            font: { size: 11, family: CHART_THEME.font },
            boxWidth: 10,
            padding: 8,
          },
        },
      },
    },
  });
}

// ── custom request tab ───────────────────────────────────────

let lastCustomRequestId = null;
let lastCustomResponse = "";

document.getElementById("custom-send").addEventListener("click", sendCustomRequest);
document.getElementById("custom-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendCustomRequest();
});

document.getElementById("custom-copy-btn").addEventListener("click", () => {
  if (!lastCustomResponse) return;
  navigator.clipboard.writeText(lastCustomResponse).then(() => showToast("Response copied")).catch(() => {});
});

async function sendCustomRequest() {
  const content = document.getElementById("custom-input").value.trim();
  if (!content) return;

  const model = document.getElementById("custom-model").value;
  const sendBtn = document.getElementById("custom-send");
  const result = document.getElementById("custom-result");
  const placeholder = document.getElementById("custom-placeholder");
  const box = document.getElementById("custom-response");
  const routing = document.getElementById("custom-routing");
  const auditBtn = document.getElementById("custom-audit-btn");
  const copyBtn = document.getElementById("custom-copy-btn");

  sendBtn.disabled = true;
  sendBtn.innerHTML = '<span class="spinner" style="width:14px;height:14px;margin:0;border-width:2px"></span> Sending…';
  result.classList.remove("hidden");
  if (placeholder) placeholder.classList.add("hidden");
  box.textContent = "";
  box.classList.add("loading");
  routing.classList.add("hidden");
  auditBtn.style.display = "none";
  copyBtn.style.display = "none";
  lastCustomResponse = "";

  const t0 = performance.now();
  try {
    const resp = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content }],
      }),
    });

    const elapsed = Math.round(performance.now() - t0);
    const tier = resp.headers.get("x-model-tier") || "—";
    const reason = resp.headers.get("x-routing-reason") || "—";
    const attempted = resp.headers.get("x-tiers-attempted") || "—";
    lastCustomRequestId = resp.headers.get("x-request-id") || null;

    box.classList.remove("loading");

    if (!resp.ok) {
      box.textContent = `HTTP ${resp.status}: ${await resp.text()}`;
    } else {
      const data = await resp.json();
      const text = data?.choices?.[0]?.message?.content || "(empty response)";
      box.textContent = text;
      lastCustomResponse = text;

      routing.innerHTML = `
        ${tierPill(tier)}
        <span class="chip">reason: <strong>${esc(reason)}</strong></span>
        <span class="chip">tiers: <strong>${esc(attempted)}</strong></span>
        <span class="chip ${latCls(elapsed)}">${fmtMs(elapsed)}</span>
      `;
      routing.classList.remove("hidden");

      copyBtn.style.display = "";
      if (lastCustomRequestId) auditBtn.style.display = "";
    }
  } catch (e) {
    box.classList.remove("loading");
    box.textContent = String(e);
  } finally {
    sendBtn.disabled = false;
    sendBtn.innerHTML = '<i data-lucide="send" width="12" height="12"></i> Send';
    lucide.createIcons({ nodes: [sendBtn] });
  }
}

document.getElementById("custom-audit-btn").addEventListener("click", () => {
  if (lastCustomRequestId) showAudit(lastCustomRequestId);
});

// ── init ──────────────────────────────────────────────────────

connectSSE();
lucide.createIcons();
