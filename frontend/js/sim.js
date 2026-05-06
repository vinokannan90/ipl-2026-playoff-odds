// Monte Carlo simulator for IPL 2026 playoff odds.
// Bug fixes vs original:
//   1. expectedWins computed cleanly (no IIFE accident).
//   2. No-result outcomes modeled (configurable, default 2.5%).
//   3. H2H tiebreak uses BOTH completed-match H2H and in-sim H2H.
//   4. Win prob resolution centralized in matchWinProb().
//   5. Pure function — no DOM, no globals — easy to test.
//
// Tiebreak order: Points → Wins → NRR (frozen at current) → H2H wins → random share.

const NR_RATE_DEFAULT = 0.025;

function matchWinProb(match, biases, llmPriors) {
  // Priority: explicit team biases > LLM prior > 50/50
  const bH = biases[match.homeId];
  const bA = biases[match.awayId];
  if (bH !== undefined && bA !== undefined) {
    return (bH + (1 - bA)) / 2;
  }
  if (bH !== undefined) return bH;
  if (bA !== undefined) return 1 - bA;
  const llm = llmPriors && llmPriors[match.matchId];
  if (llm && typeof llm.pHome === "number") {
    return Math.max(0.01, Math.min(0.99, llm.pHome));
  }
  return 0.5;
}

export function teamWinProb(match, teamId, biases, llmPriors) {
  const pHome = matchWinProb(match, biases, llmPriors);
  return match.homeId === teamId ? pHome : 1 - pHome;
}

export function simulate({
  standings,
  remaining,
  completedH2H,
  nSims = 20000,
  biases = {},
  llmPriors = null,
  nrRate = NR_RATE_DEFAULT,
}) {
  const ids = standings.map((t) => t.id);
  const n = ids.length;
  const idx = Object.fromEntries(ids.map((id, i) => [id, i]));

  const baseW = standings.map((t) => t.wins);
  const baseL = standings.map((t) => t.loss);
  const baseNR = standings.map((t) => t.nr);
  const basePts = standings.map((t) => t.pts);
  const baseNrr = standings.map((t) => t.nrr);

  const playoffCount = new Array(n).fill(0);
  const top2Count = new Array(n).fill(0);
  const finishPos = ids.map(() => new Array(n).fill(0));
  const winsAccum = new Array(n).fill(0);
  const ptsAccum = new Array(n).fill(0);

  const rem = remaining.filter(
    (m) => idx[m.homeId] !== undefined && idx[m.awayId] !== undefined
  );

  // Precompute per-match home win prob to avoid recomputing per sim
  const pHomeArr = rem.map((m) => matchWinProb(m, biases, llmPriors));

  for (let s = 0; s < nSims; s++) {
    const w = baseW.slice();
    const l = baseL.slice();
    const nr = baseNR.slice();
    const pts = basePts.slice();
    const simH2H = {}; // {winnerId: {loserId: count}}

    for (let mi = 0; mi < rem.length; mi++) {
      const m = rem[mi];
      const hi = idx[m.homeId];
      const ai = idx[m.awayId];
      const r = Math.random();
      if (r < nrRate) {
        nr[hi]++; nr[ai]++; pts[hi]++; pts[ai]++;
        continue;
      }
      const homeWins = (r - nrRate) / (1 - nrRate) < pHomeArr[mi];
      if (homeWins) {
        w[hi]++; pts[hi] += 2; l[ai]++;
        (simH2H[m.homeId] = simH2H[m.homeId] || {})[m.awayId] =
          (simH2H[m.homeId][m.awayId] || 0) + 1;
      } else {
        w[ai]++; pts[ai] += 2; l[hi]++;
        (simH2H[m.awayId] = simH2H[m.awayId] || {})[m.homeId] =
          (simH2H[m.awayId][m.homeId] || 0) + 1;
      }
    }

    // Combined H2H (completed + in-sim)
    const h2hWins = (winnerId, loserId) =>
      ((completedH2H[winnerId] && completedH2H[winnerId][loserId]) || 0) +
      ((simH2H[winnerId] && simH2H[winnerId][loserId]) || 0);

    const order = ids.map((_, i) => i).sort((a, b) => {
      if (pts[b] !== pts[a]) return pts[b] - pts[a];
      if (w[b] !== w[a]) return w[b] - w[a];
      if (baseNrr[b] !== baseNrr[a]) return baseNrr[b] - baseNrr[a];
      const ha = h2hWins(ids[a], ids[b]);
      const hb = h2hWins(ids[b], ids[a]);
      if (hb !== ha) return hb - ha;
      return Math.random() - 0.5;
    });

    for (let pos = 0; pos < order.length; pos++) {
      const ti = order[pos];
      finishPos[ti][pos]++;
      if (pos < 4) playoffCount[ti]++;
      if (pos < 2) top2Count[ti]++;
      winsAccum[ti] += w[ti];
      ptsAccum[ti] += pts[ti];
    }
  }

  return {
    nSims,
    rows: ids.map((id, i) => ({
      teamId: id,
      team: standings[i],
      playoffProb: playoffCount[i] / nSims,
      top2Prob: top2Count[i] / nSims,
      finishDist: finishPos[i].map((c) => c / nSims),
      expectedWins: winsAccum[i] / nSims,
      expectedPoints: ptsAccum[i] / nSims,
    })),
  };
}

// Single-pass conditional analysis: for a given focus team, compute
// P(focus makes playoffs | match X resolves home-win) and similarly for
// away-win, for every remaining match where the focus team is NOT playing.
// Uses the same simulated season, so cost is O(nSims * matches) — same as
// simulate(), no per-match re-runs.
export function rootingAnalysis({
  standings, remaining, completedH2H, focusTeamId,
  nSims = 5000, biases = {}, llmPriors = null, nrRate = NR_RATE_DEFAULT,
}) {
  const ids = standings.map((t) => t.id);
  const n = ids.length;
  const idx = Object.fromEntries(ids.map((id, i) => [id, i]));
  const fi = idx[focusTeamId];
  if (fi === undefined) return null;
  const baseW = standings.map((t) => t.wins);
  const baseL = standings.map((t) => t.loss);
  const baseNR = standings.map((t) => t.nr);
  const basePts = standings.map((t) => t.pts);
  const baseNrr = standings.map((t) => t.nrr);
  const rem = remaining.filter(
    (m) => idx[m.homeId] !== undefined && idx[m.awayId] !== undefined &&
           m.homeId !== focusTeamId && m.awayId !== focusTeamId
  );
  const remAll = remaining.filter(
    (m) => idx[m.homeId] !== undefined && idx[m.awayId] !== undefined
  );
  const pHomeAll = remAll.map((m) => matchWinProb(m, biases, llmPriors));
  // Map each "other team" match index in rem -> its index in remAll
  const remToAllIdx = rem.map((m) => remAll.indexOf(m));

  const homeCnt = new Array(rem.length).fill(0);
  const awayCnt = new Array(rem.length).fill(0);
  const homePlayoff = new Array(rem.length).fill(0);
  const awayPlayoff = new Array(rem.length).fill(0);

  for (let s = 0; s < nSims; s++) {
    const w = baseW.slice(), l = baseL.slice(), nr = baseNR.slice(), pts = basePts.slice();
    const simH2H = {};
    const outcomes = new Array(remAll.length); // 'H' | 'A' | 'N'
    for (let mi = 0; mi < remAll.length; mi++) {
      const m = remAll[mi];
      const hi = idx[m.homeId], ai = idx[m.awayId];
      const r = Math.random();
      if (r < nrRate) {
        outcomes[mi] = "N";
        nr[hi]++; nr[ai]++; pts[hi]++; pts[ai]++;
        continue;
      }
      const homeWins = (r - nrRate) / (1 - nrRate) < pHomeAll[mi];
      if (homeWins) {
        outcomes[mi] = "H";
        w[hi]++; pts[hi] += 2; l[ai]++;
        (simH2H[m.homeId] = simH2H[m.homeId] || {})[m.awayId] =
          (simH2H[m.homeId][m.awayId] || 0) + 1;
      } else {
        outcomes[mi] = "A";
        w[ai]++; pts[ai] += 2; l[hi]++;
        (simH2H[m.awayId] = simH2H[m.awayId] || {})[m.homeId] =
          (simH2H[m.awayId][m.homeId] || 0) + 1;
      }
    }
    const h2hWins = (winnerId, loserId) =>
      ((completedH2H[winnerId] && completedH2H[winnerId][loserId]) || 0) +
      ((simH2H[winnerId] && simH2H[winnerId][loserId]) || 0);
    const order = ids.map((_, i) => i).sort((a, b) => {
      if (pts[b] !== pts[a]) return pts[b] - pts[a];
      if (w[b] !== w[a]) return w[b] - w[a];
      if (baseNrr[b] !== baseNrr[a]) return baseNrr[b] - baseNrr[a];
      const ha = h2hWins(ids[a], ids[b]);
      const hb = h2hWins(ids[b], ids[a]);
      if (hb !== ha) return hb - ha;
      return Math.random() - 0.5;
    });
    const focusPos = order.indexOf(fi);
    const focusInPlayoffs = focusPos < 4;
    // Tally per "other team" match
    for (let ri = 0; ri < rem.length; ri++) {
      const oc = outcomes[remToAllIdx[ri]];
      if (oc === "H") { homeCnt[ri]++; if (focusInPlayoffs) homePlayoff[ri]++; }
      else if (oc === "A") { awayCnt[ri]++; if (focusInPlayoffs) awayPlayoff[ri]++; }
    }
  }

  const out = [];
  for (let ri = 0; ri < rem.length; ri++) {
    if (homeCnt[ri] < 30 || awayCnt[ri] < 30) continue; // not enough samples
    const pH = homePlayoff[ri] / homeCnt[ri];
    const pA = awayPlayoff[ri] / awayCnt[ri];
    const swing = Math.abs(pH - pA);
    const helpfulSide = pH >= pA ? "home" : "away";
    out.push({
      match: rem[ri],
      pIfHomeWins: pH,
      pIfAwayWins: pA,
      swing,
      helpfulSide,
      helpfulCode: helpfulSide === "home" ? rem[ri].homeCode : rem[ri].awayCode,
      hurtfulCode: helpfulSide === "home" ? rem[ri].awayCode : rem[ri].homeCode,
    });
  }
  // Sort by date (nearest first), with ties broken by swing descending.
  out.sort((a, b) => {
    const da = a.match.sortKey || a.match.displayDate || "";
    const db = b.match.sortKey || b.match.displayDate || "";
    if (da !== db) return da < db ? -1 : 1;
    return b.swing - a.swing;
  });
  return { focusTeamId, nSims, matches: out };
}