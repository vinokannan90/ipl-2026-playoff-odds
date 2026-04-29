// Application orchestrator. Wires modules together.

import * as api from "./api.js";
import { loadDirect } from "./jsonp.js";
import { normalizeStandings, buildScheduleModel } from "./model.js";
import { simulate, teamWinProb } from "./sim.js";
import { renderStandings, renderTeamView, renderAgentAnswer, renderLeverage } from "./render.js";

const CACHE_KEY = "iplodds:v1:data";
const CACHE_TTL_MS = 5 * 60 * 1000;

const STATE = {
  standings: [],
  remaining: [],
  completedH2H: {},
  byId: {},
  biases: {},
  llmPriors: null,
  lastResult: null,
  source: "unknown",
};

const $ = (id) => document.getElementById(id);

function setStatus(msg, isErr = false) {
  const el = $("status");
  el.textContent = msg;
  el.className = isErr ? "meta err" : "meta";
}

function readCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || !obj.t || Date.now() - obj.t > CACHE_TTL_MS) return null;
    return obj.data;
  } catch { return null; }
}

function writeCache(data) {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify({ t: Date.now(), data })); }
  catch { /* quota or disabled — non-fatal */ }
}

async function loadAll(forceRefresh = false) {
  setStatus("Fetching live data…");

  if (!forceRefresh) {
    const cached = readCache();
    if (cached) {
      hydrate(cached.standings, cached.schedule, "cache");
      setStatus(`Loaded from cache · ${STATE.standings.length} teams · ${STATE.remaining.length} matches remaining`);
      runSimulation();
      // refresh in background
      loadAll(true).catch(() => {});
      return;
    }
  }

  let standingsRaw, scheduleRaw, source;
  try {
    if (api.backendConfigured()) {
      const [s, sch] = await Promise.all([api.getStandings(), api.getSchedule()]);
      standingsRaw = s.points || s;
      scheduleRaw = sch.Matchsummary || sch;
      source = "backend";
    } else {
      const d = await loadDirect();
      standingsRaw = d.standings;
      scheduleRaw = d.schedule;
      source = "direct";
    }
  } catch (e) {
    // Last-ditch fallback: try direct JSONP
    try {
      const d = await loadDirect();
      standingsRaw = d.standings;
      scheduleRaw = d.schedule;
      source = "direct-fallback";
    } catch (e2) {
      setStatus("Failed to load live data: " + e.message, true);
      console.error(e, e2);
      return;
    }
  }

  hydrate(standingsRaw, scheduleRaw, source);
  writeCache({ standings: standingsRaw, schedule: scheduleRaw });

  // Best-effort LLM priors load (non-blocking)
  if ($("useLLMPriors").checked && api.backendConfigured()) {
    api.getPriors().then((p) => {
      STATE.llmPriors = p && p.priors ? p.priors : p;
      runSimulation();
    }).catch((e) => {
      console.warn("LLM priors unavailable:", e.message);
    });
  }

  setStatus(`Loaded ${STATE.standings.length} teams · ${STATE.remaining.length} remaining · source: ${source}`);
  runSimulation();
}

function hydrate(standingsRaw, scheduleRaw, source) {
  STATE.standings = normalizeStandings(standingsRaw);
  STATE.byId = Object.fromEntries(STATE.standings.map((t) => [t.id, t]));
  const sm = buildScheduleModel(scheduleRaw, STATE.byId);
  STATE.remaining = sm.remaining;
  STATE.completedH2H = sm.completedH2H;
  STATE.source = source;

  populateTeamSelect();
  $("dataMeta").textContent =
    `Standings rows: ${STATE.standings.length} · Remaining matches: ${STATE.remaining.length}` +
    ` · Source: ${source} · Refreshed: ${new Date().toLocaleString()}`;
}

function populateTeamSelect() {
  const sel = $("teamSel");
  const prev = sel.value;
  sel.innerHTML = "";
  const sorted = STATE.standings.slice().sort((a, b) => a.name.localeCompare(b.name));
  sorted.forEach((t) => {
    const o = document.createElement("option");
    o.value = t.id;
    o.textContent = `${t.name} (${t.code})`;
    sel.appendChild(o);
  });
  if (prev && STATE.byId[prev]) sel.value = prev;

  // Leverage focus dropdown
  const lev = $("leverageTeam");
  if (lev) {
    const prevLev = lev.value;
    lev.innerHTML = `<option value="">League-wide</option>`;
    sorted.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.code;
      o.textContent = `${t.name} (${t.code})`;
      lev.appendChild(o);
    });
    lev.value = prevLev || "";
  }
}

function runSimulation() {
  const n = Math.max(1000, Math.min(200000, parseInt($("nSims").value, 10) || 20000));
  setStatus(`Running ${n.toLocaleString()} simulations…`);
  setTimeout(() => {
    const t0 = performance.now();
    STATE.lastResult = simulate({
      standings: STATE.standings,
      remaining: STATE.remaining,
      completedH2H: STATE.completedH2H,
      nSims: n,
      biases: STATE.biases,
      llmPriors: $("useLLMPriors").checked ? STATE.llmPriors : null,
    });
    const ms = (performance.now() - t0).toFixed(0);
    setStatus(`Simulated ${n.toLocaleString()} seasons in ${ms} ms · ${STATE.remaining.length} matches remaining · ${STATE.source}`);
    renderStandings(STATE.lastResult, $("standingsTable"));
    renderTeamView(
      STATE.lastResult,
      $("teamSel").value,
      STATE.remaining,
      STATE.biases,
      $("useLLMPriors").checked ? STATE.llmPriors : null,
      $("teamView"),
      $("teamFixtures"),
      teamWinProb,
    );
  }, 20);
}

async function handleAgentSubmit(e) {
  e.preventDefault();
  const q = $("agentQ").value.trim();
  if (!q) return;
  if (!api.backendConfigured()) {
    renderAgentAnswer({ text: "Agent requires a backend deployment. Set window.__API_BASE__ or deploy the FastAPI service." }, $("agentAnswer"));
    return;
  }
  $("agentAnswer").textContent = "Thinking…";
  try {
    const ans = await api.askAgent(q);
    renderAgentAnswer(ans, $("agentAnswer"));
  } catch (err) {
    renderAgentAnswer({ text: "Agent error: " + err.message }, $("agentAnswer"));
  }
}

async function handleLeverage() {
  const status = $("leverageStatus");
  const mount = $("leverageTable");
  if (!api.backendConfigured()) {
    mount.innerHTML = `<p class="small">Leverage requires a backend deployment. Set <code>window.__API_BASE__</code> or deploy the FastAPI service.</p>`;
    return;
  }
  const team = $("leverageTeam").value || null;
  const topN = Math.max(1, Math.min(10, parseInt($("leverageTopN").value, 10) || 5));
  status.textContent = "Computing…";
  $("leverageBtn").disabled = true;
  try {
    const data = await api.getLeverage({ team, topN });
    renderLeverage(data, mount, team);
    status.textContent = `${data.matches.length} matches · ${data.nSims.toLocaleString()} sims${data.withPriors ? " · LLM priors" : ""}`;
  } catch (err) {
    status.textContent = "";
    mount.innerHTML = `<p class="small">Leverage error: ${err.message}</p>`;
  } finally {
    $("leverageBtn").disabled = false;
  }
}

function wire() {
  $("runBtn").addEventListener("click", runSimulation);
  $("reloadBtn").addEventListener("click", () => loadAll(true));
  $("teamSel").addEventListener("change", () => {
    const id = $("teamSel").value;
    const b = STATE.biases[id];
    $("teamWin").value = b !== undefined ? Math.round(b * 100) : 50;
    if (STATE.lastResult) {
      renderTeamView(
        STATE.lastResult, id, STATE.remaining, STATE.biases,
        $("useLLMPriors").checked ? STATE.llmPriors : null,
        $("teamView"), $("teamFixtures"), teamWinProb,
      );
    }
  });
  $("applyBias").addEventListener("click", () => {
    const id = $("teamSel").value;
    const v = Math.max(0, Math.min(100, parseFloat($("teamWin").value))) / 100;
    if (Math.abs(v - 0.5) < 1e-9) delete STATE.biases[id];
    else STATE.biases[id] = v;
    runSimulation();
  });
  $("useLLMPriors").addEventListener("change", () => {
    if ($("useLLMPriors").checked && !STATE.llmPriors && api.backendConfigured()) {
      api.getPriors().then((p) => {
        STATE.llmPriors = p && p.priors ? p.priors : p;
        runSimulation();
      }).catch((e) => console.warn("priors:", e.message));
    } else {
      runSimulation();
    }
  });
  $("agentForm").addEventListener("submit", handleAgentSubmit);
  const levBtn = $("leverageBtn");
  if (levBtn) levBtn.addEventListener("click", handleLeverage);
}

wire();
loadAll();
