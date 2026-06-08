/* inference-arbiter console */

const MAX_LIVE_EVENTS = 100;
const BANDIT_COLD_START = 500; // obs per tier before policy activates

const liveFeed = document.getElementById("live-feed");
const compareBar = document.getElementById("compare-bar");
const liveEmpty = document.getElementById("live-empty");
const sseStatus = document.getElementById("sse-status");

let benchChart = null;
let benchPollTimer = null;
let metricsPollTimer = null;
let benchStartTime = null;
let benchDuration = 0;
let benchSpawnRate = 0;
let benchUsers = 0;
const charts = {};

// ---------- helpers ----------

function tierClass(tier) {
  if (!tier) return "tier-unknown";
  return `tier-${tier.toLowerCase()}`;
}

function latencyClass(ms) {
  if (ms < 500) return "fast";
  if (ms < 2000) return "mid";
  return "slow";
}

function formatMs(ms) {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function pct(a, b) {
  if (!b || !a) return null;
  return Math.round(((b - a) / b) * 100);
}

// ---------- tabs ----------

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

// ---------- sliders ----------

["users", "ramp", "duration"].forEach((key) => {
  const input = document.getElementById(`bench-${key}`);
  const label = document.getElementById(`val-${key}`);
  input.addEventListener("input", () => { label.textContent = input.value; });
});

// ---------- scenario hints ----------

const SCENARIO_HINTS = {
  baseline: "Every request is sent to the large (8b) model — no routing intelligence. Use as the cost/latency ceiling.",
  arbiter: "Requests are routed by the LinUCB bandit across all three tiers based on complexity and SLO. The system under test.",
  round_robin: "Requests cycle evenly across small / medium / large models. Simulates naive load distribution.",
};

const scenarioSelect = document.getElementById("bench-scenario");
const scenarioHint = document.getElementById("scenario-hint");

function updateScenarioHint() {
  scenarioHint.textContent = SCENARIO_HINTS[scenarioSelect.value] || "";
}
scenarioSelect.addEventListener("change", updateScenarioHint);
updateScenarioHint();

// ---------- health poll ----------

async function pollHealth() {
  try {
    const resp = await fetch("/console/api/health");
    const data = await resp.json();
    const gw = document.getElementById("pill-gateway");
    gw.textContent = `gateway ${data.status || "ok"}`;
    gw.className = "pill ok";
    const bd = document.getElementById("pill-bandit");
    const active = data.bandit_policy_active;
    bd.textContent = active ? "bandit learned" : "bandit heuristic";
    bd.className = `pill ${active ? "ok" : "warn"}`;
  } catch {
    const gw = document.getElementById("pill-gateway");
    gw.textContent = "gateway offline";
    gw.className = "pill warn";
  }
}
setInterval(pollHealth, 5000);
pollHealth();

// ---------- SSE live feed ----------

function setSSEStatus(connected) {
  if (connected) {
    sseStatus.classList.add("hidden");
  } else {
    sseStatus.classList.remove("hidden");
  }
}

function connectSSE() {
  const source = new EventSource("/console/api/events");
  source.onmessage = (ev) => {
    setSSEStatus(true);
    try {
      const event = JSON.parse(ev.data);
      prependEvent(event);
    } catch (e) {
      console.error("SSE parse error:", e);
    }
  };
  source.onerror = () => {
    setSSEStatus(false);
    source.close();
    setTimeout(connectSSE, 3000);
  };
}

function prependEvent(event) {
  // Hide empty state on first event
  if (liveEmpty) liveEmpty.classList.add("hidden");

  const card = document.createElement("div");
  card.className = "event-card";
  const tier = event.final_tier || "unknown";
  const cascade =
    event.tiers_attempted && event.tiers_attempted.length > 1
      ? event.tiers_attempted.join(" → ")
      : tier;

  const priorityBadge = event.priority
    ? `<span class="priority-badge priority-${event.priority.toLowerCase()}">${event.priority}</span>`
    : "";

  card.innerHTML = `
    <span class="tier-badge ${tierClass(tier)}">${tier}</span>
    <div class="event-body">
      <div class="event-route">
        <strong>${escapeHtml(event.requested_model || "auto")}</strong> → ${escapeHtml(cascade)}
        ${event.degraded ? ' <span class="degraded-tag">degraded</span>' : ""}
        ${priorityBadge}
      </div>
      <div class="event-preview">${escapeHtml(event.prompt_preview || "")}</div>
    </div>
    <div class="event-meta">
      <div class="latency ${latencyClass(event.elapsed_ms)}">${formatMs(event.elapsed_ms)}</div>
      <div class="routing-reason">${escapeHtml(event.routing_reason || "")}</div>
    </div>
  `;
  card.addEventListener("click", () => showAudit(event.request_id));
  liveFeed.prepend(card);
  while (liveFeed.children.length > MAX_LIVE_EVENTS + 1) {
    // +1 because liveEmpty is a child
    const last = liveFeed.lastChild;
    if (last && last !== liveEmpty) liveFeed.removeChild(last);
  }
}

// ---------- audit modal ----------

async function showAudit(requestId) {
  const modal = document.getElementById("audit-modal");
  const pre = document.getElementById("audit-json");
  const spinner = document.getElementById("audit-spinner");
  pre.textContent = "";
  spinner.classList.remove("hidden");
  modal.classList.remove("hidden");
  try {
    const resp = await fetch(`/console/api/routing/${requestId}`);
    const data = await resp.json();
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

document.getElementById("audit-copy").addEventListener("click", () => {
  const text = document.getElementById("audit-json").textContent;
  if (!text) return;
  navigator.clipboard.writeText(text).catch(() => {});
  const btn = document.getElementById("audit-copy");
  const orig = btn.textContent;
  btn.textContent = "Copied!";
  setTimeout(() => { btn.textContent = orig; }, 1500);
});

// Close modal on backdrop click
document.getElementById("audit-modal").addEventListener("click", (e) => {
  if (e.target === document.getElementById("audit-modal")) {
    document.getElementById("audit-modal").classList.add("hidden");
  }
});

// ---------- benchmark ----------

document.getElementById("bench-start").addEventListener("click", startBenchmark);
document.getElementById("bench-stop").addEventListener("click", stopBenchmark);

async function startBenchmark() {
  const scenario = document.getElementById("bench-scenario").value;
  const users = parseInt(document.getElementById("bench-users").value, 10);
  const spawn_rate = parseFloat(document.getElementById("bench-ramp").value);
  const duration_s = parseFloat(document.getElementById("bench-duration").value);

  benchStartTime = Date.now();
  benchDuration = duration_s;
  benchSpawnRate = spawn_rate;
  benchUsers = users;

  document.getElementById("bench-start").disabled = true;
  document.getElementById("bench-stop").disabled = false;
  document.getElementById("stat-running").textContent = "starting";

  // Show ramp-up progress bar
  const rampBar = document.getElementById("bench-ramp-bar");
  rampBar.classList.remove("hidden");
  const rampSeconds = Math.ceil(users / spawn_rate);
  document.getElementById("bench-ramp-label").textContent =
    `Spawning workers… (${rampSeconds}s ramp-up)`;

  initBenchChart();

  await fetch("/console/api/benchmark/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario, users, spawn_rate, duration_s }),
  });

  benchPollTimer = setInterval(pollBenchmark, 1000);
}

async function stopBenchmark() {
  await fetch("/console/api/benchmark/stop", { method: "POST" });
  clearInterval(benchPollTimer);
  document.getElementById("bench-start").disabled = false;
  document.getElementById("bench-stop").disabled = true;
  document.getElementById("bench-ramp-bar").classList.add("hidden");
  pollBenchmark();
}

async function pollBenchmark() {
  try {
    const resp = await fetch("/console/api/benchmark/status");
    const data = await resp.json();

    document.getElementById("stat-requests").textContent = data.requests;
    document.getElementById("stat-failures").textContent = data.failures;
    document.getElementById("stat-rps").textContent =
      typeof data.rps === "number" ? data.rps.toFixed(1) : "0";
    document.getElementById("stat-p50").textContent = formatMs(data.p50_ms);
    document.getElementById("stat-p95").textContent = formatMs(data.p95_ms);

    // Update ramp-up progress bar
    if (benchStartTime && data.running) {
      const elapsed = (Date.now() - benchStartTime) / 1000;
      const rampSeconds = Math.ceil(benchUsers / benchSpawnRate);
      if (elapsed < rampSeconds) {
        const pctDone = Math.min(elapsed / rampSeconds, 1);
        document.getElementById("bench-ramp-fill").style.width = `${pctDone * 100}%`;
        document.getElementById("bench-ramp-label").textContent =
          `Spawning workers… ${Math.round(pctDone * benchUsers)}/${benchUsers}`;
        document.getElementById("stat-running").textContent = "ramping up";
      } else {
        document.getElementById("bench-ramp-bar").classList.add("hidden");
        document.getElementById("stat-running").textContent = "running";
      }
    }

    if (benchChart && data.running) {
      const elapsed = benchStartTime ? (Date.now() - benchStartTime) / 1000 : 0;
      benchChart.data.labels.push(`${Math.round(elapsed)}s`);
      benchChart.data.datasets[0].data.push(
        typeof data.rps === "number" ? data.rps : 0
      );
      if (benchChart.data.labels.length > 60) {
        benchChart.data.labels.shift();
        benchChart.data.datasets[0].data.shift();
      }
      benchChart.update("none");
    }

    if (!data.running && benchPollTimer) {
      document.getElementById("bench-start").disabled = false;
      document.getElementById("bench-stop").disabled = true;
      document.getElementById("bench-ramp-bar").classList.add("hidden");
      document.getElementById("stat-running").textContent = "idle";
      clearInterval(benchPollTimer);
      benchPollTimer = null;
    }

    updateCompare(data.completed_runs || {});
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
        label: "RPS",
        data: [],
        borderColor: "#58a6ff",
        backgroundColor: "rgba(88,166,255,0.1)",
        fill: true,
        tension: 0.3,
        pointRadius: 0,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: {
          beginAtZero: true,
          grid: { color: "#30363d" },
          ticks: { color: "#8b949e" },
          title: { display: true, text: "req/s", color: "#8b949e", font: { size: 11 } },
        },
      },
    },
  });
}

function updateCompare(runs) {
  const baseline = runs.baseline;
  const arbiter = runs.arbiter;
  if (!baseline && !arbiter) return;

  compareBar.classList.add("has-data");

  if (baseline && arbiter) {
    const p50Delta = pct(arbiter.p50_ms, baseline.p50_ms);
    const p95Delta = pct(arbiter.p95_ms, baseline.p95_ms);
    const rpsDelta = baseline.rps > 0
      ? Math.round(((arbiter.rps - baseline.rps) / baseline.rps) * 100)
      : null;

    const fmtDelta = (d, unit) => {
      if (d == null) return "";
      const sign = d > 0 ? "−" : "+";
      return ` <span class="${d > 0 ? "delta-good" : "delta-bad"}">${sign}${Math.abs(d)}% ${unit}</span>`;
    };

    compareBar.innerHTML =
      `<strong>Baseline</strong> P50 ${formatMs(baseline.p50_ms)} · P95 ${formatMs(baseline.p95_ms)} · ${baseline.rps} rps` +
      `&emsp;vs&emsp;` +
      `<strong>Arbiter</strong> P50 ${formatMs(arbiter.p50_ms)}${fmtDelta(p50Delta, "latency")} · P95 ${formatMs(arbiter.p95_ms)}${fmtDelta(p95Delta, "")} · ${arbiter.rps} rps${fmtDelta(rpsDelta, "throughput")}`;
  } else if (baseline) {
    compareBar.textContent =
      `Baseline done — P50 ${formatMs(baseline.p50_ms)} · P95 ${formatMs(baseline.p95_ms)} · ${baseline.rps} rps. Run Arbiter to compare.`;
  } else {
    compareBar.textContent =
      `Arbiter done — P50 ${formatMs(arbiter.p50_ms)} · P95 ${formatMs(arbiter.p95_ms)} · ${arbiter.rps} rps. Run Baseline to compare.`;
  }
}

// ---------- metrics ----------

function startMetricsPoll() {
  pollMetrics();
  metricsPollTimer = setInterval(pollMetrics, 5000);
}

function stopMetricsPoll() {
  clearInterval(metricsPollTimer);
}

async function pollMetrics() {
  try {
    const resp = await fetch("/console/api/metrics/summary");
    const data = await resp.json();
    const banner = document.getElementById("metrics-banner");

    if (!data.available) {
      banner.textContent = `Prometheus unavailable: ${data.error || "not reachable"}. Start with: docker compose up -d`;
      banner.classList.remove("hidden");
      return;
    }
    banner.classList.add("hidden");

    renderBarChart("chart-tier-rate", data.tier_rate, "req/s");
    renderPieChart("chart-reasons", data.routing_reasons);
    renderBarChart("chart-cost", data.cost_rate, "cost/s");

    // SLO
    const sloEl = document.getElementById("metric-slo");
    if (data.slo_attainment_rate != null) {
      const pctVal = (data.slo_attainment_rate * 100).toFixed(1);
      sloEl.textContent = `${pctVal}%`;
      sloEl.style.color = data.slo_attainment_rate >= 0.95 ? "var(--green)"
        : data.slo_attainment_rate >= 0.8 ? "var(--amber)"
        : "var(--red)";
    } else {
      sloEl.textContent = "—";
    }
    document.getElementById("metric-slo-detail").textContent =
      `${data.slo_met || 0} met / ${data.slo_evaluated || 0} evaluated`;

    // Bandit — with per-tier progress bars
    const active = data.bandit_policy_active;
    document.getElementById("metric-bandit").textContent = active ? "Active" : "Heuristic";
    document.getElementById("metric-bandit").style.color = active ? "var(--green)" : "var(--amber)";

    const obs = data.bandit_observations || {};
    const banditObsEl = document.getElementById("metric-bandit-obs");
    if (Object.keys(obs).length === 0) {
      banditObsEl.textContent = "No observations yet";
    } else {
      banditObsEl.innerHTML = Object.entries(obs).map(([tier, count]) => {
        const n = Math.round(count);
        const barPct = Math.min((n / BANDIT_COLD_START) * 100, 100);
        const barColor = n >= BANDIT_COLD_START ? "var(--green)" : "var(--accent)";
        return `
          <div class="bandit-tier-row">
            <span class="bandit-tier-label">${tier}</span>
            <div class="bandit-bar-wrap">
              <div class="bandit-bar-fill" style="width:${barPct}%;background:${barColor}"></div>
            </div>
            <span class="bandit-tier-count">${n} / ${BANDIT_COLD_START}</span>
          </div>`;
      }).join("");
    }

    // Endpoint health
    const epEl = document.getElementById("metric-endpoints");
    const inFlight = data.endpoint_in_flight || {};
    const circuits = data.circuit_breaker_state || {};
    if (Object.keys(inFlight).length === 0) {
      epEl.innerHTML = '<span style="color:var(--muted)">No endpoint data</span>';
    } else {
      epEl.innerHTML = Object.keys(inFlight).map((name) => {
        const cbVal = circuits[name];
        const cbState = cbVal === 0 ? "closed" : cbVal === 1 ? "half-open" : "open";
        const cbColor = cbVal === 0 ? "var(--green)" : cbVal === 1 ? "var(--amber)" : "var(--red)";
        return `<div class="endpoint-row">
          <span>${name}</span>
          <span>in-flight <strong>${Math.round(inFlight[name])}</strong> · CB <span style="color:${cbColor}">${cbState}</span></span>
        </div>`;
      }).join("");
    }

  } catch (e) {
    const banner = document.getElementById("metrics-banner");
    banner.textContent = String(e);
    banner.classList.remove("hidden");
  }
}

function renderBarChart(canvasId, series, yLabel) {
  const ctx = document.getElementById(canvasId);
  const labels = Object.keys(series);
  const values = Object.values(series);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: yLabel,
        data: values,
        backgroundColor: ["#3fb950", "#d29922", "#f85149", "#58a6ff"],
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: "#30363d" },
          ticks: { color: "#8b949e" },
          title: { display: true, text: yLabel, color: "#8b949e", font: { size: 11 } },
        },
        x: { ticks: { color: "#8b949e" } },
      },
    },
  });
}

function renderPieChart(canvasId, series) {
  const ctx = document.getElementById(canvasId);
  const labels = Object.keys(series);
  const values = Object.values(series);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#a371f7"],
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "right", labels: { color: "#8b949e", font: { size: 11 } } } },
    },
  });
}

connectSSE();
