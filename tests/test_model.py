"""Sanity checks that run against the offline cache.

    python tests/make_test_cache.py <csv_dir> /tmp/testcache
    python tests/test_model.py /tmp/testcache
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from fpl_model.api import FPLClient                                   # noqa: E402
from fpl_model.features import (Season, build_fixtures, build_players,  # noqa: E402
                                build_teams, expected_conceded_penalty,
                                parse_events)
from fpl_model.optimiser import (FORMATIONS, SQUAD_LIMITS, best_xi_for_gw,  # noqa: E402
                                 optimise_squad, squad_horizon_points,
                                 suggest_transfers)
from fpl_model.projection import gw_matrix, project                   # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label} {detail}")


def main(cache: str) -> int:
    print("formations")
    check("eight legal outfield shapes", len(FORMATIONS) == 8, str(FORMATIONS))
    check("every shape fields ten outfielders",
          all(sum(f) == 10 for f in FORMATIONS))

    print("\nconceded deduction")
    check("no deduction is positive",
          all(expected_conceded_penalty(x) <= 0 for x in (0.5, 1.0, 2.0, 3.0)))
    check("harder fixtures cost more",
          expected_conceded_penalty(3.0) < expected_conceded_penalty(1.0))

    print("\nloading offline season")
    client = FPLClient(cache_dir=cache, offline=True, verbose=False)
    boot, fxj = client.bootstrap(), client.fixtures()
    events, next_gw, current_gw, played = parse_events(boot)
    teams = build_teams(boot)
    fixtures = build_fixtures(fxj, teams, next_gw, 5)
    summaries = client.element_summaries([int(e["id"]) for e in boot["elements"]])
    players = build_players(boot, summaries, teams, played)
    season = Season(players=players, teams=teams.reset_index(), fixtures=fixtures,
                    events=events, next_gw=next_gw, current_gw=current_gw,
                    gws_played=played)

    check("every team has 20 clubs", len(teams) == 20, str(len(teams)))
    check("each club has a fixture in the next gameweek",
          fixtures[fixtures.gw == next_gw].team.nunique() == 20)
    check("expected goals are plausible",
          bool(fixtures.xg_for.between(0.3, 3.7).all()),
          f"{fixtures.xg_for.min():.2f}-{fixtures.xg_for.max():.2f}")
    check("home sides are favoured on average",
          fixtures[fixtures.is_home].xg_for.mean()
          > fixtures[~fixtures.is_home].xg_for.mean())

    print("\nplayer features")
    check("no missing scoring rates", not players.base_p90.isna().any())
    check("no missing availability", not players.availability.isna().any())
    check("point shares sum to one",
          bool(np.allclose(players.att_share + players.cs_share
                           + players.neutral_share, 1.0)))
    check("expected minutes within a match",
          bool(players.xmins.between(0, 92).all()),
          f"max {players.xmins.max():.1f}")
    check("forwards earn nothing from clean sheets",
          float(players[players.pos == "FWD"].cs_share.max()) == 0.0)

    print("\nprojections")
    per_gw, summary = project(season, horizon=5)
    check("no missing projections", not summary.ep_horizon.isna().any())
    check("projections are non-negative", bool((per_gw.ep >= 0).all()))
    check("single-gameweek scores are sane",
          bool(summary.ep_next.max() < 15), f"max {summary.ep_next.max():.1f}")
    check("premium players out-project cheap ones",
          float(summary[summary.price >= 10].ep_next.mean())
          > float(summary[summary.price <= 4.5].ep_next.mean()))
    check("injured players are marked down",
          bool((summary[summary.availability == 0].ep_next == 0).all()))

    gws = sorted(per_gw.gw.unique())[:5]
    gw_ep = gw_matrix(per_gw, summary.id.tolist(), gws)

    print("\nsquad optimisation")
    result = optimise_squad(summary, budget=100.0, verbose=False)
    squad = result["squad"]
    ix = summary.set_index("id")
    check("fifteen players", len(squad) == 15)
    check("inside the budget", result["cost"] <= 100.0 + 1e-6, str(result["cost"]))
    check("spends most of the budget", result["cost"] >= 95.0, str(result["cost"]))
    counts = ix.loc[squad].pos.value_counts().to_dict()
    check("positional quotas met", counts == SQUAD_LIMITS, str(counts))
    club_max = int(ix.loc[squad].team.value_counts().max())
    check("at most three per club", club_max <= 3, str(club_max))

    print("\nlineup")
    pos = ix.pos.to_dict()
    ep_now = {p: gw_ep.get(p, {}).get(gws[0], 0.0) for p in squad}
    line = best_xi_for_gw(squad, pos, ep_now)
    check("eleven starters", len(line.xi) == 11)
    check("four on the bench", len(line.bench) + (1 if line.bench_gk else 0) == 4)
    check("exactly one keeper starts",
          sum(1 for p in line.xi if pos[p] == "GK") == 1)
    check("captain is in the XI", line.captain in line.xi)
    check("captain is the highest projected starter",
          ep_now[line.captain] == max(ep_now[p] for p in line.xi))
    check("bench is ordered by projection",
          all(ep_now[a] >= ep_now[b]
              for a, b in zip(line.bench, line.bench[1:])))
    check("no XI beats the chosen one",
          all(best_xi_for_gw(squad, pos, ep_now).xi_points >= alt
              for alt in [line.xi_points]))

    print("\ntransfers")
    tr, base, value = suggest_transfers(squad, summary, gw_ep, gws,
                                        bank=2.0, free_transfers=1, top_n=8)
    check("horizon score is positive", base > 0, f"{base:.1f}")
    check("an already-optimal squad has few upgrades", len(tr) <= 8, str(len(tr)))
    check("every suggestion is affordable", all(t["cost"] <= 2.0 + 1e-9 for t in tr))
    check("hits are subtracted",
          all(abs(t["net_gain"] - (t["gain"] - t["hit"])) < 1e-9 for t in tr))
    check("suggestions are ranked",
          all(a["net_gain"] >= b["net_gain"] for a, b in zip(tr, tr[1:])))

    # A deliberately weak squad should have obvious upgrades available.
    cheap = []
    for p, n in SQUAD_LIMITS.items():
        cheap += ix[ix.pos == p].nsmallest(n * 3, "ep_horizon").index.tolist()[:n]
    weak_tr, weak_base, _ = suggest_transfers(cheap, summary, gw_ep, gws,
                                              bank=50.0, free_transfers=1)
    check("a weak squad scores less than an optimised one", weak_base < base,
          f"{weak_base:.1f} vs {base:.1f}")
    check("a weak squad has an upgrade worth making",
          bool(weak_tr) and weak_tr[0]["net_gain"] > 1.0)

    print("\ndegraded team strength ratings")
    # Regression: FPL ships the attack/defence ratings as zeros until it has
    # set the season's numbers. Dividing by that produced NaN everywhere,
    # every projection tied at zero, and the ranking fell back to the API's
    # own club-by-club ordering - which read as "the model only likes Arsenal".
    import copy

    from fpl_model.features import build_teams as _bt

    zeroed = copy.deepcopy(boot)
    for t in zeroed["teams"]:
        for c in ("strength_attack_home", "strength_attack_away",
                  "strength_defence_home", "strength_defence_away"):
            t[c] = 0
        t["strength"] = None
    blank = copy.deepcopy(zeroed)
    for t in blank["teams"]:
        t["strength_overall_home"] = t["strength_overall_away"] = 0

    for label, payload, want in (("overall rating only", zeroed, "overall rating only"),
                                 ("nothing usable", blank, "fixture difficulty only")):
        tm = _bt(payload)
        check(f"{label}: falls back correctly",
              tm.attrs["strength_source"] == want, tm.attrs["strength_source"])
        check(f"{label}: no missing team ratings",
              not tm[["att_home", "att_away", "def_home", "def_away"]].isna().any().any())
        fxd = build_fixtures(fxj, tm, next_gw, 5)
        check(f"{label}: expected goals stay valid",
              bool(fxd.xg_for.notna().all() and fxd.xg_for.between(0.3, 3.7).all()),
              f"{fxd.xg_for.min():.2f}-{fxd.xg_for.max():.2f}")
        s2 = Season(players=players, teams=tm.reset_index(), fixtures=fxd,
                    events=events, next_gw=next_gw, current_gw=current_gw,
                    gws_played=played)
        _, sum2 = project(s2, horizon=5)
        check(f"{label}: projections do not collapse",
              float(sum2.ep_horizon.max()) > 5.0, f"max {sum2.ep_horizon.max():.2f}")
        check(f"{label}: top 15 is not one club",
              int(sum2.head(15).team_short.nunique()) >= 5,
              str(sum2.head(15).team_short.value_counts().to_dict()))
        sq2 = optimise_squad(sum2, budget=100.0, verbose=False)["squad"]
        check(f"{label}: squad still respects three per club",
              int(sum2.set_index("id").loc[sq2].team_short.value_counts().max()) <= 3)

    check("healthy data still uses the full ratings",
          build_teams(boot).attrs["strength_source"] == "attack/defence split")
    check("top 15 spans several clubs on healthy data",
          int(summary.head(15).team_short.nunique()) >= 5,
          str(summary.head(15).team_short.value_counts().to_dict()))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/testcache"))
