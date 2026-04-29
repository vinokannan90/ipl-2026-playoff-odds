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
