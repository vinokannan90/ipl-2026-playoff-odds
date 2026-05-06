// Application orchestrator. Wires modules together.

import * as api from "./api.js";
import { loadDirect } from "./jsonp.js";
import { normalizeStandings, buildScheduleModel } from "./model.js";
import { simulate, teamWinProb, rootingAnalysis } from "./sim.js";
import { renderStandings, renderTeamView, renderAgentAnswer, renderLeverage, renderRooting } from "./render.js";

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
  latestBadgeHtml: "",
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

function setStatus(msg, isErr = false) {
  const el = $("status");
  if (!el) return;
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
  STATE.latestCompleted = sm.latestCompleted || null;
  STATE.source = source;

  populateTeamSelect();
  $("dataMeta").textContent =
    `Standings rows: ${STATE.standings.length} · Remaining matches: ${STATE.remaining.length}` +
    ` · Source: ${source} · Refreshed: ${new Date().toLocaleString()}`;

  // Season summary line above the table.
  // IPL league stage: 10 teams × 14 matches / 2 sides = 70 matches.
  const totalMatches = 70;
  const playedTotal = Math.round(
    STATE.standings.reduce((acc, t) => acc + t.matches, 0) / 2
  );
  const remaining = totalMatches - playedTotal;
  const refreshed = new Date().toLocaleString("en-IN", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: true,
  });
  const summaryEl = $("seasonSummary");
  if (summaryEl) {
    let badgeHtml = "";
    const lc = STATE.latestCompleted;
    if (lc && lc.resultText) {
      // Build "GT beat PBKS · Won by 4 Wickets" from available fields
      const loserCode = lc.winnerCode
        ? (lc.winnerCode === lc.homeCode ? lc.awayCode : lc.homeCode)
        : null;
      const wonIdx = lc.resultText.indexOf(" Won ");
      const margin = wonIdx !== -1 ? lc.resultText.slice(wonIdx + 1) : lc.resultText;
      const label = lc.winnerCode && loserCode
        ? `${lc.winnerCode} beat ${loserCode} · ${margin}`
        : lc.resultText;
      badgeHtml = `<span class="latest-result">Latest: <strong>${escapeHtml(label)}</strong></span>`;
    }
    STATE.latestBadgeHtml = badgeHtml;
    summaryEl.innerHTML =
      `<strong>${playedTotal}</strong> of ${totalMatches} league matches played · ` +
      `<strong>${remaining}</strong> remaining · ` +
      `Last updated <span class="small">${refreshed}</span>` +
      ` · <span id="liveOrLatest">${badgeHtml}</span>` +
      (source === "cache" ? ` <span class="small">· from cache</span>` : "");
    // Non-blocking live check — replaces badge if a match is in progress
    if (api.backendConfigured()) checkLive().catch(() => {});
  }
}

// ---- Live score helpers ----

let livePoller = null;

function buildLiveScoreHtml(m) {
  const innings = m.innings || [];
  const currentInnings = m.currentInnings || 1;
  let scoreText = "";
  const matchup = `${escapeHtml(m.homeCode)} vs ${escapeHtml(m.awayCode)}`;
  if (!innings.length) {
    scoreText = matchup;
  } else {
    const active = innings.find(i => i.inningNum === currentInnings);
    const completed = innings.filter(i => i.inningNum !== currentInnings);
    const parts = [matchup];
    completed.forEach(i => {
      const overs = i.overs ? ` (${escapeHtml(i.overs)})` : "";
      parts.push(`${escapeHtml(i.teamCode)} ${escapeHtml(i.score)}${overs}`);
    });
    if (active) {
      const overs = active.overs ? ` · ${escapeHtml(active.overs)} Ov` : "";
      parts.push(`${escapeHtml(active.teamCode)} batting - ${escapeHtml(active.score)}${overs}`);
    }
    scoreText = parts.join(" | ");
    if (m.chasingText) scoreText += ` | ${escapeHtml(m.chasingText)}`;
  }
  return `<span class="live-score">Live: <strong>${scoreText}</strong></span>`;
}

function startLivePolling() {
  if (livePoller) return;
  livePoller = setInterval(() => checkLive().catch(() => {}), 30_000);
}

function stopLivePolling() {
  if (livePoller) { clearInterval(livePoller); livePoller = null; }
}

async function checkLive() {
  const data = await api.getLive();
  const live = (data && data.live) || [];
  const badge = document.getElementById("liveOrLatest");
  if (!badge) return;
  if (!live.length) {
    // Match just ended — restore latest-result badge and stop polling
    badge.innerHTML = STATE.latestBadgeHtml;
    stopLivePolling();
    return;
  }
  // Show the match closest to finishing (already sorted server-side)
  badge.innerHTML = buildLiveScoreHtml(live[0]);
  startLivePolling();
}

// ---- End live score helpers ----

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
  const n = 20000;
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
    setStatus(`Simulated ${n.toLocaleString()} seasons in ${ms} ms`);
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
    handleRooting();
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
    renderAgentAnswer({ text: "Agent error: " + escapeHtml(err.message) }, $("agentAnswer"));
  }
}

function handleRooting() {
  const id = $("teamSel").value;
  const status = $("rootingStatus");
  const mount = $("rootingTable");
  if (!id || !STATE.standings.length) {
    status.textContent = "Pick a team first.";
    return;
  }
  const team = STATE.byId[id];
  status.textContent = "Replaying season…";
  mount.innerHTML = "";
  $("rootingBtn").disabled = true;
  // Defer to next tick so the status text paints.
  setTimeout(() => {
    try {
      const t0 = performance.now();
      const data = rootingAnalysis({
        standings: STATE.standings,
        remaining: STATE.remaining,
        completedH2H: STATE.completedH2H,
        focusTeamId: id,
        nSims: 5000,
        biases: STATE.biases,
        llmPriors: $("useLLMPriors").checked ? STATE.llmPriors : null,
      });
      const ms = (performance.now() - t0).toFixed(0);
      renderRooting(data, team, mount);
      status.textContent = `${data.nSims.toLocaleString()} sims · ${ms} ms`;
    } catch (err) {
      mount.innerHTML = `<p class="small">Rooting error: ${escapeHtml(err.message)}</p>`;
      status.textContent = "";
    } finally {
      $("rootingBtn").disabled = false;
    }
  }, 20);
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
    mount.innerHTML = `<p class="small">Leverage error: ${escapeHtml(err.message)}</p>`;
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
    // Auto-compute rooting analysis (in-browser, fast).
    if (STATE.lastResult && STATE.standings.length) handleRooting();
    else { $("rootingTable").innerHTML = ""; $("rootingStatus").textContent = ""; }
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

  const rootBtn = $("rootingBtn");
  if (rootBtn) rootBtn.addEventListener("click", handleRooting);
}

wire();
loadAll();
