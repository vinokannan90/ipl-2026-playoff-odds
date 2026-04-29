"""Unit tests for the Python simulator + leverage math."""

from __future__ import annotations

import pytest

from iplodds.sim import (
    Match,
    Team,
    remaining_from_raw,
    simulate_with_leverage,
    teams_from_raw,
)


def _three_teams() -> list[Team]:
    return [
        Team(id="1", code="AAA", name="Alpha", pts=4, wins=2, loss=0, nr=0, nrr=0.5),
        Team(id="2", code="BBB", name="Beta", pts=2, wins=1, loss=1, nr=0, nrr=0.0),
        Team(id="3", code="CCC", name="Gamma", pts=0, wins=0, loss=2, nr=0, nrr=-0.5),
    ]


def test_teams_from_raw_basic():
    raw = [{"TeamID": 7, "TeamCode": "RCB", "TeamName": "RCB", "Points": 6, "Wins": 3, "Loss": 1, "NoResult": 0, "NetRunRate": 0.4}]
    out = teams_from_raw(raw)
    assert len(out) == 1
    assert out[0].id == "7" and out[0].code == "RCB" and out[0].pts == 6


def test_remaining_skips_completed_and_collects_h2h():
    raw = [
        {"MatchID": "m1", "MatchStatus": "Post", "HomeTeamID": "1", "AwayTeamID": "2", "MatchWinnerID": "1"},
        {"MatchID": "m2", "MatchStatus": "Upcoming", "HomeTeamID": "2", "AwayTeamID": "3", "HomeTeamCode": "B", "AwayTeamCode": "C", "MatchDate": "2026-04-01"},
    ]
    by_id = {t.id: t for t in _three_teams()}
    rem, h2h = remaining_from_raw(raw, by_id)
    assert len(rem) == 1
    assert rem[0].match_id == "m2"
    assert h2h.get("1", {}).get("2") == 1


def test_simulate_returns_expected_shape_and_bounds():
    teams = _three_teams()
    remaining = [
        Match(match_id="m1", home_id="1", away_id="2", home_code="AAA", away_code="BBB", date="2026-04-01"),
        Match(match_id="m2", home_id="2", away_id="3", home_code="BBB", away_code="CCC", date="2026-04-02"),
    ]
    out = simulate_with_leverage(
        teams=teams,
        remaining=remaining,
        completed_h2h={},
        n_sims=2000,
        rng_seed=42,
    )
    assert out["nSims"] == 2000
    assert {t["code"] for t in out["teams"]} == {"AAA", "BBB", "CCC"}
    for t in out["teams"]:
        assert 0.0 <= t["playoffProb"] <= 1.0
        assert 0.0 <= t["top2Prob"] <= 1.0
    assert len(out["leverage"]) == 2
    for m in out["leverage"]:
        assert 0.0 <= m["totalLeverage"]
        assert m["samplesHomeWin"] + m["samplesAwayWin"] <= 2000
        for swing in m["perTeam"].values():
            assert 0.0 <= swing <= 1.0


def test_simulate_is_deterministic_with_seed():
    teams = _three_teams()
    remaining = [
        Match(match_id="m1", home_id="1", away_id="2", home_code="AAA", away_code="BBB", date="2026-04-01"),
    ]
    a = simulate_with_leverage(teams=teams, remaining=remaining, completed_h2h={}, n_sims=500, rng_seed=7)
    b = simulate_with_leverage(teams=teams, remaining=remaining, completed_h2h={}, n_sims=500, rng_seed=7)
    assert a["leverage"] == b["leverage"]


def test_simulate_with_priors_shifts_swing():
    """When prior strongly favors home, samples_home_win >> samples_away_win."""
    teams = _three_teams()
    remaining = [
        Match(match_id="m1", home_id="1", away_id="3", home_code="AAA", away_code="CCC", date="2026-04-01"),
    ]
    out = simulate_with_leverage(
        teams=teams,
        remaining=remaining,
        completed_h2h={},
        n_sims=2000,
        priors={"m1": {"pHome": 0.9}},
        rng_seed=11,
    )
    m = out["leverage"][0]
    assert m["samplesHomeWin"] > m["samplesAwayWin"] * 3


@pytest.mark.parametrize("p_home", [0.1, 0.5, 0.9])
def test_p_home_reported_matches_prior(p_home):
    teams = _three_teams()
    remaining = [
        Match(match_id="m1", home_id="1", away_id="2", home_code="AAA", away_code="BBB", date="2026-04-01"),
    ]
    out = simulate_with_leverage(
        teams=teams,
        remaining=remaining,
        completed_h2h={},
        n_sims=200,
        priors={"m1": {"pHome": p_home}},
    )
    assert abs(out["leverage"][0]["pHome"] - p_home) < 1e-3
