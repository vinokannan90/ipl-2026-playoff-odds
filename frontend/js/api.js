// Backend API client. Falls back to direct JSONP if backend unreachable
// so the page still works when deployed as a pure static demo.

// Resolved at build/deploy time via Static Web App config injection.
// During local dev, set window.__API_BASE__ = "http://localhost:8000" before app.js loads.
const API_BASE = (typeof window !== "undefined" && window.__API_BASE__) || "";

const TIMEOUT_MS = 15000;
const AGENT_TIMEOUT_MS = 45000;

async function fetchJSON(path, opts = {}, timeoutMs = TIMEOUT_MS) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      signal: ctrl.signal,
      headers: { Accept: "application/json", ...(opts.headers || {}) },
      credentials: "omit",
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} ${res.statusText}`);
    }
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

export async function getStandings() {
  return fetchJSON("/api/standings");
}

export async function getSchedule() {
  return fetchJSON("/api/schedule");
}

export async function getPriors() {
  // Returns { matchId: { pHome: number, rationale: string } }
  return fetchJSON("/api/priors");
}

export async function getLeverage({ team = null, topN = 5 } = {}) {
  const qs = new URLSearchParams();
  if (team) qs.set("team", team);
  if (topN) qs.set("top_n", String(topN));
  return fetchJSON(`/api/leverage?${qs.toString()}`);
}

export async function askAgent(question) {
  return fetchJSON("/api/agent/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  }, AGENT_TIMEOUT_MS);
}

export async function getLive() {
  return fetchJSON("/api/live");
}

export async function getLatestResult() {
  return fetchJSON("/api/latest-result");
}

export function backendConfigured() {
  return Boolean(API_BASE);
}
