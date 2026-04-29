// Pure functions for parsing the iplt20 feed shapes into our internal model.

export function normalizeStandings(rawPoints) {
  return (rawPoints || []).map((r) => ({
    raw: r,
    id: String(r.TeamID),
    code: r.TeamCode,
    name: r.TeamName,
    logo: r.TeamLogo,
    matches: parseInt(r.Matches, 10) || 0,
    wins: parseInt(r.Wins, 10) || 0,
    loss: parseInt(r.Loss, 10) || 0,
    nr: parseInt(r.NoResult, 10) || 0,
    pts: parseInt(r.Points, 10) || 0,
    nrr: parseFloat(r.NetRunRate) || 0,
  }));
}

// Parse the iplt20 raw date string. Format historically: "DD MMM YYYY" or ISO-ish.
// We try Date.parse first, fall back to a deterministic NaN-safe order.
function parseMatchDate(s) {
  if (!s) return Number.MAX_SAFE_INTEGER;
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : Number.MAX_SAFE_INTEGER;
}

export function buildScheduleModel(rawSchedule, byId) {
  const remaining = [];
  const completedH2H = {}; // {winnerId: {loserId: count}}

  for (const m of rawSchedule || []) {
    const status = (m.MatchStatus || "").toLowerCase();
    const homeId = String(m.HomeTeamID || m.FirstBattingTeamID || "");
    const awayId = String(m.AwayTeamID || m.SecondBattingTeamID || "");
    if (!homeId || !awayId) continue;

    const isCompleted = ["post", "completed", "result"].includes(status);
    if (isCompleted) {
      const winnerId = String(m.MatchWinnerID || "");
      if (winnerId && (winnerId === homeId || winnerId === awayId)) {
        const loserId = winnerId === homeId ? awayId : homeId;
        completedH2H[winnerId] = completedH2H[winnerId] || {};
        completedH2H[winnerId][loserId] = (completedH2H[winnerId][loserId] || 0) + 1;
      }
      continue;
    }

    remaining.push({
      matchId: String(m.MatchID || ""),
      displayDate: m.MatchDateNew || m.MatchDate || "",
      sortKey: parseMatchDate(m.MatchDate),
      homeId,
      awayId,
      homeCode: m.HomeTeamCode || byId[homeId]?.code || "?",
      awayCode: m.AwayTeamCode || byId[awayId]?.code || "?",
      name: m.MatchName || "",
    });
  }

  remaining.sort((a, b) => a.sortKey - b.sortKey);
  return { remaining, completedH2H };
}
