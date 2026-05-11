// Pure functions for parsing the iplt20 feed shapes into our internal model.

export function normalizeStandings(rawPoints) {
  return (rawPoints || []).map((r) => ({
    raw: r,
    id: String(r.TeamID),
    code: r.TeamCode,
    name: r.TeamName,
    // The iplt20 feed sometimes returns short-lived S3 URLs for newer
    // franchises (RCB/RR/GT/LSG) which 404 or violate our CSP. The
    // canonical CDN path keyed by team code is stable for all 10 teams.
    logo: r.TeamCode ? `https://scores.iplt20.com/ipl/teamlogos/${r.TeamCode}.png` : r.TeamLogo,
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
  let latestCompleted = null; // {date, homeCode, awayCode, winnerCode, resultText, sortKey}

  for (const m of rawSchedule || []) {
    const status = (m.MatchStatus || "").toLowerCase();
    const homeId = String(m.HomeTeamID || m.FirstBattingTeamID || "");
    const awayId = String(m.AwayTeamID || m.SecondBattingTeamID || "");
    if (!homeId || !awayId) continue;

    const isCompleted = ["post", "completed", "result"].includes(status);
    if (isCompleted) {
      const winnerId = String(m.MatchWinnerID || m.WinningTeamID || "");
      if (winnerId && (winnerId === homeId || winnerId === awayId)) {
        const loserId = winnerId === homeId ? awayId : homeId;
        completedH2H[winnerId] = completedH2H[winnerId] || {};
        completedH2H[winnerId][loserId] = (completedH2H[winnerId][loserId] || 0) + 1;
      }
      // Track the most recently played match for display
      const sk = parseMatchDate(m.MatchDate);
      if (!latestCompleted || sk > latestCompleted.sortKey) {
        // IMPORTANT: never mix HomeTeamCode with FirstBattingTeamCode.
        // homeId derives from HomeTeamID (venue/schedule assignment, pre-toss).
        // FirstBattingTeamCode is determined by the toss and may be the *away* team.
        // Mixing them causes winnerCode to resolve to the wrong team when the
        // away side wins the toss and bats first.
        const homeCode = m.HomeTeamCode || byId[homeId]?.code || "?";
        const awayCode = m.AwayTeamCode || byId[awayId]?.code || "?";
        const winnerCode = winnerId
          ? (winnerId === homeId ? homeCode : awayCode)
          : null;
        latestCompleted = {
          sortKey: sk,
          date: m.MatchDateNew || m.MatchDate || "",
          homeCode,
          awayCode,
          winnerCode,
          resultText: m.Comments || m.MatchResultTxt || m.ResultTxt || "",
          matchName: m.MatchName || "",
        };
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
  return { remaining, completedH2H, latestCompleted };
}
