"""Smoke tests for the priors agent payload builder + sanitization."""

from iplodds.agents.priors import _build_user_payload


def test_build_user_payload_filters_completed():
    standings = {"points": [
        {"TeamID": 1, "TeamCode": "AAA", "TeamName": "A", "Points": 4, "Wins": 2, "Loss": 1, "NoResult": 0, "NetRunRate": 0.1},
        {"TeamID": 2, "TeamCode": "BBB", "TeamName": "B", "Points": 2, "Wins": 1, "Loss": 2, "NoResult": 0, "NetRunRate": -0.2},
    ]}
    schedule = {"Matchsummary": [
        {"MatchID": 1, "MatchStatus": "Post", "HomeTeamID": 1, "AwayTeamID": 2, "MatchWinnerID": 1, "MatchDate": "2026-04-10"},
        {"MatchID": 2, "MatchStatus": "Upcoming", "HomeTeamID": 2, "AwayTeamID": 1, "HomeTeamCode": "BBB", "AwayTeamCode": "AAA", "MatchDate": "2026-05-01"},
    ]}
    out = _build_user_payload(standings, schedule)
    assert len(out["teams"]) == 2
    assert len(out["remaining"]) == 1
    assert out["remaining"][0]["matchId"] == "2"
