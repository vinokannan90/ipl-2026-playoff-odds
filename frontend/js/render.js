// Rendering helpers — pure DOM, no business logic.

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

export function renderStandings(result, mountEl) {
  const rows = result.rows.slice().sort((a, b) =>
    b.team.pts - a.team.pts || b.team.nrr - a.team.nrr
  );
  let html = `<table><thead><tr>
    <th scope="col">Team</th>
    <th scope="col" class="num">P</th>
    <th scope="col" class="num">W</th>
    <th scope="col" class="num">L</th>
    <th scope="col" class="num">NR</th>
    <th scope="col" class="num">Pts</th>
    <th scope="col" class="num">NRR</th>
    <th scope="col" style="text-align:center">Top 4 %</th>
    <th scope="col" title="Playoff race based on simulated chance of finishing in the top 4" style="border-left:2px solid var(--border)">Race</th>
    <th scope="col" class="num top2-cell" style="border-left:2px solid var(--border)">Top 2</th>
  </tr></thead><tbody>`;

  for (const r of rows) {
    const t = r.team;
    const pct = (r.playoffProb * 100).toFixed(1);
    const top2 = (r.top2Prob * 100).toFixed(1);
    // 5 outlook buckets keyed off playoff probability (top-4 finish chance).
    const pctNum = r.playoffProb * 100;
    let pillCls, pillTxt;
    if (pctNum <= 0)        { pillCls = "out";   pillTxt = "Out of Race"; }
    else if (pctNum <= 10)  { pillCls = "unl";   pillTxt = "Unlikely"; }
    else if (pctNum <= 50)  { pillCls = "fight"; pillTxt = "Fighting"; }
    else if (pctNum <= 80)  { pillCls = "race";  pillTxt = "In the Race"; }
    else                    { pillCls = "top";   pillTxt = "Almost Through"; }
    const pillTip =
      "Playoff race (chance of finishing top 4):\n" +
      "✕ Out of Race    — 0%\n" +
      "◌ Unlikely       — 0.1% to 10%\n" +
      "◐ Fighting       — 10.1% to 50%\n" +
      "● In the Race    — 50.1% to 80%\n" +
      "★ Almost Through — above 80%";
    html += `<tr>
      <th scope="row"><div class="row team-cell">
        <img class="logo" src="${escapeHTML(t.logo)}" alt="${escapeHTML(t.code)} logo" loading="lazy" />
        <div class="team-text">
          <strong class="team-code">${escapeHTML(t.code)}</strong>
          <span class="small team-name">${escapeHTML(t.name)}</span>
        </div>
      </div></th>
      <td class="num">${t.matches}</td>
      <td class="num">${t.wins}</td>
      <td class="num">${t.loss}</td>
      <td class="num">${t.nr}</td>
      <td class="num"><strong>${t.pts}</strong></td>
      <td class="num">${t.nrr.toFixed(3)}</td>
      <td>
        <div class="prob">
            <div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Top 4 probability ${pct}%">
            <span style="width:${pct}%"></span>
          </div>
          <strong class="prob-pct">${pct}%</strong>
        </div>
      </td>
      <td style="border-left:2px solid var(--border)"><span class="pill ${pillCls}" title="${escapeHTML(pillTxt + ' — ' + pillTip)}" aria-label="${escapeHTML(pillTxt)}" tabindex="0">${pillTxt}</span></td>
      <td class="num top2-cell" style="border-left:2px solid var(--border)">${top2}%</td>
    </tr>`;
  }
  html += "</tbody></table>";
  html += `<p class="footnote" style="margin-top:8px">
    Race (chance of top-4 finish across ${result.nSims.toLocaleString()} simulations):
    <span class="pill pill-legend out">Out of Race</span> 0% ·
    <span class="pill pill-legend unl">Unlikely</span> 0.1–10% ·
    <span class="pill pill-legend fight">Fighting</span> 10.1–50% ·
    <span class="pill pill-legend race">In the Race</span> 50.1–80% ·
    <span class="pill pill-legend top">Almost Through</span> &gt;80% — not a mathematical guarantee.
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

  // "Wins needed" tile — full-width, top of the grid
  // 16 pts is the historical heuristic for safe passage;
  // not a mathematical guarantee (5 teams could theoretically all reach 16).
  const SAFE_PTS = 16;
  const ptsNeeded = Math.max(0, SAFE_PTS - t.pts);
  const winsNeeded = Math.ceil(ptsNeeded / 2);
  if (t.pts >= SAFE_PTS) {
    html += `<div class="scenario wins-needed safe" style="grid-column:1/-1">
      <div class="small">16-pt historically safe mark</div>
      <div class="v">✓ Already there</div>
      <div class="small">${t.pts} pts — past the heuristic threshold</div>
    </div>`;
  } else if (maxPts < SAFE_PTS) {
    html += `<div class="scenario wins-needed out" style="grid-column:1/-1">
      <div class="small">16-pt historically safe mark</div>
      <div class="v">— Cannot reach ${SAFE_PTS} pts</div>
      <div class="small">Max possible: ${maxPts} pts from ${matchesLeft} remaining matches</div>
    </div>`;
  } else {
    html += `<div class="scenario wins-needed" style="grid-column:1/-1" title="16 pts is a historical heuristic, not a mathematical guarantee — NRR can still matter in a tie">
      <div class="small">Wins needed to reach the 16-pt safe mark ⚑</div>
      <div class="v">${winsNeeded} <span class="small">of ${matchesLeft} remaining</span></div>
      <div class="small">${t.pts} pts now · need ${ptsNeeded} more pts · max possible: ${maxPts}</div>
    </div>`;
  }
  html += `<p class="footnote" style="grid-column:1/-1; margin:0 0 4px">
    ⚑ 16 pts = historically safe in IPL, but not a mathematical guarantee —
    if 4 other teams also reach 16, NRR breaks the tie. 18+ pts is near-certain.
  </p>`;

  html += `<div class="scenario"><div class="small">Playoff probability</div><div class="v">${(row.playoffProb*100).toFixed(1)}%</div></div>`;
  html += `<div class="scenario"><div class="small">Top-2 probability</div><div class="v">${(row.top2Prob*100).toFixed(1)}%</div></div>`;
  html += `<div class="scenario"><div class="small">Expected final wins</div><div class="v">${Math.round(row.expectedWins)}</div><div class="small">from ${t.wins} now + ${matchesLeft} left</div></div>`;
  html += `<div class="scenario"><div class="small">Expected final points</div><div class="v">${Math.round(row.expectedPoints)}</div><div class="small">max possible: ${maxPts}</div></div>`;

  html += `<div class="scenario" style="grid-column:1/-1"><div class="small" style="margin-bottom:6px">Finish position distribution</div>`;
  for (let pos = 0; pos < row.finishDist.length; pos++) {
    const p = row.finishDist[pos];
    const w = (p * 100).toFixed(1);
    const cls = pos < 4 ? "in" : "out";
    html += `<div style="display:flex; align-items:center; gap:8px; margin:2px 0">
      <span class="pill pill-pos ${cls}" style="width:42px; text-align:center">${pos + 1}</span>
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

const TEAM_LOGO = (code) =>
  code ? `https://scores.iplt20.com/ipl/teamlogos/${code}.png` : "";

export function renderRooting(data, focusTeam, mountEl) {
  if (!data || !data.matches || !data.matches.length) {
    mountEl.innerHTML = `<p class="small">No other-team matches with enough samples to analyze.</p>`;
    return;
  }
  const focusName = focusTeam ? focusTeam.name : "Your team";
  const top = data.matches.slice(0, 7);
  // For "rooting against" (avoid winners), pick matches whose helpful side gives focus the
  // SMALLEST boost — i.e. the hurtful side is most damaging. Reuse the swing list, but
  // present these as "if X wins, your odds drop most".
  // Simplest: show the top 3 by swing as "key matches"; helpfulCode is who to root for.
  const matchRow = (m, kind) => {
    const helpful = m.helpfulCode;
    const hurtful = m.hurtfulCode;
    const pH = (m.pIfHomeWins * 100).toFixed(1);
    const pA = (m.pIfAwayWins * 100).toFixed(1);
    const swingPP = (m.swing * 100).toFixed(1);
    const date = m.match.displayDate || "";
    const homeLogo = TEAM_LOGO(m.match.homeCode);
    const awayLogo = TEAM_LOGO(m.match.awayCode);
    // Show helpful side first in the swing line
    const firstCode  = m.helpfulSide === "home" ? m.match.homeCode : m.match.awayCode;
    const firstPct   = m.helpfulSide === "home" ? pH : pA;
    const secondCode = m.helpfulSide === "home" ? m.match.awayCode : m.match.homeCode;
    const secondPct  = m.helpfulSide === "home" ? pA : pH;
    return `<div class="rooting-row ${kind}">
      <div class="rooting-date small">${escapeHTML(date)}</div>
      <div class="rooting-match">
        <img class="logo logo-sm" src="${escapeHTML(homeLogo)}" alt=""/>
        <strong>${escapeHTML(m.match.homeCode)}</strong>
        <span class="small">vs</span>
        <strong>${escapeHTML(m.match.awayCode)}</strong>
        <img class="logo logo-sm" src="${escapeHTML(awayLogo)}" alt=""/>
      </div>
      <div class="rooting-verdict">
        <span class="pill race">Root for ${escapeHTML(helpful)}</span>
        <span class="pill out" title="If ${escapeHTML(hurtful)} wins, ${escapeHTML(focusName)}'s playoff odds drop">Avoid ${escapeHTML(hurtful)}</span>
      </div>
      <div class="rooting-swing small">
        ${escapeHTML(firstCode)} wins → <strong>${firstPct}%</strong> ·
        ${escapeHTML(secondCode)} wins → <strong>${secondPct}%</strong> ·
        swing <strong>${swingPP} pp</strong>
      </div>
    </div>`;
  };
  let html = `<p class="small" style="margin:8px 0 4px">
    Upcoming other-team matches in date order — showing the swing on
    <strong>${escapeHTML(focusName)}</strong>'s playoff odds. Root for the
    highlighted side to boost your chances.
  </p>`;
  html += `<div class="rooting-list">${top.map((m) => matchRow(m, "key")).join("")}</div>`;
  html += `<p class="footnote" style="margin-top:8px">
    Computed by replaying ${data.nSims.toLocaleString()} seasons and bucketing each match's
    outcome by whether ${escapeHTML(focusName)} qualified. "Root for" = the result that
    most often coincides with you making playoffs.
  </p>`;
  mountEl.innerHTML = html;
}
