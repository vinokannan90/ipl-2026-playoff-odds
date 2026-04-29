// Rendering helpers — pure DOM, no business logic.

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

export function renderStandings(result, mountEl) {
  const rows = result.rows.slice().sort((a, b) => b.playoffProb - a.playoffProb);
  let html = `<table><thead><tr>
    <th scope="col">Team</th>
    <th scope="col" class="num">P</th>
    <th scope="col" class="num">W</th>
    <th scope="col" class="num">L</th>
    <th scope="col" class="num">NR</th>
    <th scope="col" class="num">Pts</th>
    <th scope="col" class="num">NRR</th>
    <th scope="col">Playoff Probability</th>
    <th scope="col" class="num">Top 2</th>
    <th scope="col"></th>
  </tr></thead><tbody>`;

  for (const r of rows) {
    const t = r.team;
    const pct = (r.playoffProb * 100).toFixed(1);
    const top2 = (r.top2Prob * 100).toFixed(1);
    let pillCls = "bub", pillTxt = "Bubble";
    if (r.playoffProb >= 0.99) { pillCls = "in"; pillTxt = "Likely"; }
    else if (r.playoffProb <= 0.01) { pillCls = "out"; pillTxt = "Unlikely"; }
    html += `<tr>
      <th scope="row"><div class="row">
        <img class="logo" src="${escapeHTML(t.logo)}" alt="${escapeHTML(t.code)} logo" loading="lazy" />
        <div><strong>${escapeHTML(t.code)}</strong> <span class="small">${escapeHTML(t.name)}</span></div>
      </div></th>
      <td class="num">${t.matches}</td>
      <td class="num">${t.wins}</td>
      <td class="num">${t.loss}</td>
      <td class="num">${t.nr}</td>
      <td class="num"><strong>${t.pts}</strong></td>
      <td class="num">${t.nrr.toFixed(3)}</td>
      <td>
        <div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">
          <span style="width:${pct}%"></span>
        </div>
        <div class="small" style="margin-top:2px">${pct}%</div>
      </td>
      <td class="num">${top2}%</td>
      <td><span class="pill ${pillCls}">${pillTxt}</span></td>
    </tr>`;
  }
  html += "</tbody></table>";
  html += `<p class="footnote" style="margin-top:8px">
    "Likely/Unlikely" reflects ≥99% / ≤1% across ${result.nSims.toLocaleString()} simulations
    under current bias settings — not a mathematical guarantee.
  </p>`;
  mountEl.innerHTML = html;
}

export function renderTeamView(result, teamId, remaining, biases, llmPriors, mountEl, fixturesEl, twpFn) {
  const row = result.rows.find((r) => r.teamId === teamId);
  if (!row) { mountEl.innerHTML = ""; fixturesEl.innerHTML = ""; return; }
  const t = row.team;
  const teamRem = remaining.filter((m) => m.homeId === teamId || m.awayId === teamId);
  const matchesLeft = teamRem.length;
  const maxPts = t.pts + matchesLeft * 2;

  let html = "";
  html += `<div class="scenario"><div class="small">Playoff probability</div><div class="v">${(row.playoffProb*100).toFixed(1)}%</div></div>`;
  html += `<div class="scenario"><div class="small">Top-2 probability</div><div class="v">${(row.top2Prob*100).toFixed(1)}%</div></div>`;
  html += `<div class="scenario"><div class="small">Expected final wins</div><div class="v">${row.expectedWins.toFixed(2)}</div><div class="small">from ${t.wins} now + ${matchesLeft} left</div></div>`;
  html += `<div class="scenario"><div class="small">Expected final points</div><div class="v">${row.expectedPoints.toFixed(2)}</div><div class="small">max possible: ${maxPts}</div></div>`;

  html += `<div class="scenario" style="grid-column:1/-1"><div class="small" style="margin-bottom:6px">Finish position distribution</div>`;
  for (let pos = 0; pos < row.finishDist.length; pos++) {
    const p = row.finishDist[pos];
    const w = (p * 100).toFixed(1);
    const cls = pos < 4 ? "in" : "out";
    html += `<div style="display:flex; align-items:center; gap:8px; margin:2px 0">
      <span class="pill ${cls}" style="width:42px; text-align:center">${pos + 1}</span>
      <div class="bar" style="flex:1"><span style="width:${w}%"></span></div>
      <span class="small" style="width:48px; text-align:right">${w}%</span>
    </div>`;
  }
  html += `</div>`;
  mountEl.innerHTML = html;

  let f = "";
  for (const m of teamRem) {
    const isHome = m.homeId === teamId;
    const opp = isHome ? m.awayCode : m.homeCode;
    const pTeam = twpFn(m, teamId, biases, llmPriors);
    f += `<div class="matchrow">
      <div class="date">${escapeHTML(m.displayDate)}</div>
      <div class="teams">${isHome ? "vs" : "@"} ${escapeHTML(opp)}</div>
      <div class="pred">P(win) = ${(pTeam * 100).toFixed(0)}%</div>
    </div>`;
  }
  if (!teamRem.length) f = '<div class="small">No remaining matches.</div>';
  fixturesEl.innerHTML = f;
}

export function renderAgentAnswer(answer, mountEl) {  // answer = { text: string, citations?: [{label, url}] }
  let html = `<div>${escapeHTML(answer.text || "")}</div>`;
  if (answer.citations && answer.citations.length) {
    html += `<div class="citations">Sources: ` +
      answer.citations.map((c) =>
        c.url
          ? `<a href="${escapeHTML(c.url)}" rel="noopener">${escapeHTML(c.label)}</a>`
          : escapeHTML(c.label)
      ).join(" · ") + `</div>`;
  }
  mountEl.innerHTML = html;
}

export function renderLeverage(payload, mountEl, focusTeam) {
  const { matches, nSims, withPriors } = payload;
  if (!matches || !matches.length) {
    mountEl.innerHTML = `<p class="small">No remaining matches.</p>`;
    return;
  }
  const focusCol = focusTeam ? focusTeam.toUpperCase() : null;
  let html = `<table><thead><tr>
    <th scope="col">Date</th>
    <th scope="col">Match</th>
    <th scope="col" class="num">P(home wins)</th>
    <th scope="col" class="num">${focusCol ? `Swing for ${escapeHTML(focusCol)}` : "Total swing"}</th>
    <th scope="col">Top movers</th>
    <th scope="col"></th>
  </tr></thead><tbody>`;
  for (const m of matches) {
    const mainSwing = focusCol
      ? (m.perTeam?.[focusCol] ?? 0)
      : m.totalLeverage;
    const sortedTeams = Object.entries(m.perTeam || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([code, v]) => `${escapeHTML(code)} ${(v * 100).toFixed(1)}pp`)
      .join(" · ");
    const conf = m.confident
      ? `<span class="pill in">solid</span>`
      : `<span class="pill bub">low N</span>`;
    html += `<tr>
      <td>${escapeHTML(m.date || "")}</td>
      <td><strong>${escapeHTML(m.home)}</strong> vs <strong>${escapeHTML(m.away)}</strong></td>
      <td class="num">${(m.pHome * 100).toFixed(0)}%</td>
      <td class="num"><strong>${(mainSwing * 100).toFixed(1)} pp</strong></td>
      <td class="small">${sortedTeams}</td>
      <td>${conf}</td>
    </tr>`;
  }
  html += `</tbody></table>`;
  html += `<p class="footnote" style="margin-top:8px">
    Swing = |P(qualify | home wins) − P(qualify | away wins)|, in percentage points.
    Single-pass conditional bucketing over ${nSims.toLocaleString()} simulations${withPriors ? " using LLM priors" : ""}.
    "low N" = fewer than 200 samples in one outcome bucket; treat with caution.
  </p>`;
  mountEl.innerHTML = html;
}
