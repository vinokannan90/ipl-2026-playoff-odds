"""Highest-leverage match finder.

Single-pass conditional bucketing: for each match m and team t,

    leverage(m, t) = | P(playoff | t, home wins m) - P(playoff | t, away wins m) |

Total leverage of a match is the sum across all teams. Per-team leverage is
returned too, so the UI can answer "which 3 matches matter most for RCB?".
"""

from __future__ import annotations

from typing import Any

from iplodds.agents import priors as priors_agent
from iplodds.config import get_settings
from iplodds.data import iplt20_client
from iplodds.sim import remaining_from_raw, simulate_with_leverage, teams_from_raw

DEFAULT_N_SIMS = 2000
MAX_N_SIMS = 20000


async def compute_leverage(
    *,
    top_n: int = 5,
    team_code: str | None = None,
    n_sims: int = DEFAULT_N_SIMS,
    use_priors: bool = True,
) -> dict[str, Any]:
    s = get_settings()
    n_sims = max(500, min(MAX_N_SIMS, int(n_sims)))

    standings_raw = await iplt20_client.get_standings()
    schedule_raw = await iplt20_client.get_schedule()

    teams = teams_from_raw(standings_raw.get("points", []))
    by_id = {t.id: t for t in teams}
    remaining, completed_h2h = remaining_from_raw(
        schedule_raw.get("Matchsummary", []), by_id
    )

    priors = None
    if use_priors and s.feature_priors:
        try:
            p = await priors_agent.compute_priors()
            priors = p.get("priors") if isinstance(p, dict) else None
        except Exception:  # noqa: BLE001
            priors = None

    result = simulate_with_leverage(
        teams=teams,
        remaining=remaining,
        completed_h2h=completed_h2h,
        n_sims=n_sims,
        priors=priors,
    )

    matches = result["leverage"]
    if team_code:
        tc = team_code.upper()
        matches = sorted(
            matches, key=lambda m: m["perTeam"].get(tc, 0.0), reverse=True
        )
    else:
        matches = sorted(matches, key=lambda m: m["totalLeverage"], reverse=True)

    return {
        "nSims": result["nSims"],
        "teamFilter": team_code.upper() if team_code else None,
        "withPriors": priors is not None,
        "matches": matches[:top_n],
    }
