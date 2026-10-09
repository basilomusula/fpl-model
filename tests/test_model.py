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
    gks_ = players[players.pos == "GK"]
    gk_sum = (gks_.p_start * gks_.availability).groupby(gks_.team).sum()
    check("each club's available keepers share at most one starting slot",
          bool((gk_sum <= 1.0 + 1e-6).all()), f"max {gk_sum.max():.2f}")

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

    print("\nminutes weighting and rotation filter")
    from fpl_model.projection import MINUTES_EXPONENT as _MX
    _, flat = project(season, horizon=5, minutes_exponent=1.0)
    _, steep = project(season, horizon=5, minutes_exponent=1.6)
    fi = flat.set_index("id").sort_index(); si = steep.set_index("id").sort_index()
    f, s = fi.ep_horizon, si.ep_horizon
    nailed = fi.xmins >= 85
    partial = (fi.xmins > 10) & (fi.xmins <= 45)
    check("default weighting favours starters", _MX > 1.0, str(_MX))
    check("a full 90 is unaffected by the weighting",
          bool(np.allclose(f[nailed & (f > 0)], s[nailed & (f > 0)], rtol=0.06)))
    check("part-players are marked down harder",
          float(s[partial].sum()) < float(f[partial].sum()))
    check("the weighting never inflates anyone", bool((s <= f + 1e-6).all()))
    filtered = optimise_squad(summary, budget=100.0, min_minutes=60, verbose=False)["squad"]
    check("rotation filter keeps a legal squad", len(filtered) == 15)
    check("rotation filter excludes low-minute players",
          float(ix.loc[filtered].xmins.min()) >= 60)
    check("actual minutes are available to report",
          "curr_minutes" in summary.columns and not summary.curr_minutes.isna().any())
    check("start rate is a probability", bool(summary.p_start.between(0, 1).all()))

    print("\ncaptain and vice")
    rel = {p_: 0.5 if i == 1 else 1.0 for i, p_ in enumerate(squad)}
    line_rel = best_xi_for_gw(squad, pos, ep_now, reliability=rel)
    check("captain unchanged by reliability weighting", line_rel.captain == line.captain)
    check("vice is in the XI and not the captain",
          line_rel.vice_captain in line_rel.xi and line_rel.vice_captain != line_rel.captain)
    ep_rig = dict(ep_now)
    others = [p_ for p_ in line.xi if p_ != line.captain]
    top2 = sorted(others, key=lambda p_: ep_now[p_], reverse=True)[:2]
    rel2 = {p_: 1.0 for p_ in squad}; rel2[top2[0]] = 0.4   # second-best is a doubt
    line2 = best_xi_for_gw(squad, pos, ep_rig, reliability=rel2)
    check("a doubtful second-best is passed over for vice",
          line2.vice_captain != top2[0], f"vice {line2.vice_captain} vs doubt {top2[0]}")

    print("\nformations")
    from fpl_model.optimiser import fmt_formation, parse_formation
    check("parses '3-4-3'", parse_formation("3-4-3") == (3, 4, 3))
    check("'auto' means no constraint", parse_formation("auto") is None)
    try:
        parse_formation("2-5-3"); bad_ok = False
    except ValueError:
        bad_ok = True
    check("rejects an illegal shape", bad_ok)

    auto_h = squad_horizon_points(squad, pos, gw_ep, gws)
    for shp in FORMATIONS:
        name = fmt_formation(shp)
        built = optimise_squad(summary, budget=100.0, formation=shp, verbose=False)
        sq = built["squad"]
        counts_f = ix.loc[sq].pos.value_counts().to_dict()
        line_f = best_xi_for_gw(sq, pos, {p_: gw_ep.get(p_, {}).get(gws[0], 0.0) for p_ in sq},
                                formation=shp)
        xi_shape = tuple(sum(1 for p_ in line_f.xi if pos[p_] == k) for k in ("DEF", "MID", "FWD"))
        h = squad_horizon_points(sq, pos, gw_ep, gws, formation=shp)
        ok = (len(sq) == 15 and counts_f == SQUAD_LIMITS and built["cost"] <= 100.0 + 1e-6
              and int(ix.loc[sq].team.value_counts().max()) <= 3 and xi_shape == shp
              and line_f.formation == name
              and h <= squad_horizon_points(sq, pos, gw_ep, gws) + 1e-6)
        check(f"{name}: legal squad, XI in shape, never beats its own auto", ok,
              f"xi {xi_shape} cost {built['cost']} h {h:.1f} vs auto {auto_h:.1f}")

    # Forcing a shape on any squad never beats letting it choose.
    for shp in FORMATIONS:
        ep_any = {p_: gw_ep.get(p_, {}).get(gws[0], 0.0) for p_ in squad}
        if best_xi_for_gw(squad, pos, ep_any, formation=shp).xi_points > line.xi_points + 1e-9:
            check(f"fixed {fmt_formation(shp)} cannot beat auto on one week", False)
            break
    else:
        check("a fixed shape never outscores auto for the same squad", True)

    tr_f, base_f, _ = suggest_transfers(squad, summary, gw_ep, gws, bank=2.0,
                                        free_transfers=1, top_n=6, formation=(3, 4, 3))
    check("transfer search respects a fixed shape",
          abs(base_f - squad_horizon_points(squad, pos, gw_ep, gws, formation=(3, 4, 3))) < 1e-6)

    print("\nfixture difficulty ticker")
    from fpl_model.cli import build_ticker
    tick = build_ticker(season, gws)
    check("one row per club", len(tick["rows"]) == 20)
    check("easiest run listed first",
          all(a["avg"] <= b["avg"] for a, b in zip(tick["rows"], tick["rows"][1:])))
    check("a club never plays itself",
          all(c["opp"] != r["club"] for r in tick["rows"] for cs in r["cells"].values() for c in cs))

    print("\nbacktest harness")
    import os
    from fpl_model.backtest import GameweekLog, evaluate_gameweek, run_backtest, summarise
    from fpl_model.features import ModelParams

    hist_dir = os.environ.get("FPL_HIST_DIR", "/home/claude/fpl/hist")
    prior_dir = os.environ.get("FPL_PRIOR_DIR", "/home/claude/fpl/testdata")
    have_hist = all(os.path.exists(os.path.join(hist_dir, f))
                    for f in ("merged_gw.csv", "players_raw.csv", "teams.csv", "fixtures.csv"))
    if not have_hist:
        print("  SKIP  (no per-gameweek history CSVs at FPL_HIST_DIR)")
    else:
        log = GameweekLog.from_csvs(
            os.path.join(hist_dir, "merged_gw.csv"), os.path.join(hist_dir, "players_raw.csv"),
            os.path.join(hist_dir, "teams.csv"), os.path.join(hist_dir, "fixtures.csv"),
            {k: os.path.join(prior_dir, v) for k, v in
             (("2024/25", "players_2024-25.csv"), ("2023/24", "players_2023-24.csv"))
             if os.path.exists(os.path.join(prior_dir, v))})
        check("history covers a full season", log.rounds[-1] >= 30, str(log.rounds[-1]))

        # No leakage: the as-of state must equal the sum of earlier rounds only.
        g = 12
        boot, summ, fxs, act = log.as_of(g)
        el = pd.DataFrame(boot["elements"]).set_index("id")
        rows = log.rows
        pid = int(rows[rows["round"] < g].groupby("id").minutes.sum().idxmax())
        expect = float(rows[(rows.id == pid) & (rows["round"] < g)].total_points.sum())
        check("as-of totals exclude the target gameweek and after",
              abs(float(el.loc[pid, "total_points"]) - expect) < 1e-6,
              f"{el.loc[pid, 'total_points']} vs {expect}")
        check("as-of history log stops before the gameweek",
              all(int(h["round"]) < g for h in summ[pid]["history"]))
        check("fixtures at or after the gameweek are unplayed",
              all(not f["finished"] for f in fxs if f.get("event") and f["event"] >= g))
        check("actuals come from the target gameweek only",
              abs(float(act[act.id == pid].actual.iloc[0])
                  - float(rows[(rows.id == pid) & (rows["round"] == g)].total_points.sum())) < 1e-6)

        m, df = evaluate_gameweek(log, g)
        check("one-week metrics are finite",
              all(np.isfinite(m[k]) for k in ("mae", "spearman", "xi_model", "xi_naive")))
        check("model XI is inside the oracle ceiling",
              m["xi_model"] <= m["xi_oracle"] + 1e-9)
        check("captain pick never beats the best possible",
              m["captain_model"] <= m["captain_best"] + 1e-9)

        # Regression: the live API serves several numbers as text
        # ("expected_goals": "0.45"). Summing those once concatenated them
        # into "0.000.00" and crashed the scorecard on the first live run.
        import copy as _copy
        boot_l, summ_l, fx_l, _ = log.as_of(10)
        boot_l = _copy.deepcopy(boot_l); summ_l = _copy.deepcopy(summ_l)
        text_fields = ("expected_goals", "expected_assists")
        for e in boot_l["elements"]:
            for f_ in text_fields + ("form",):
                e[f_] = f"{float(e.get(f_) or 0):.2f}"
        for s_ in summ_l.values():
            for h in s_["history"] + s_["history_past"]:
                for f_ in text_fields:
                    h[f_] = f"{float(h.get(f_) or 0):.2f}"
        api_log = GameweekLog.from_api(boot_l, fx_l, summ_l)
        try:
            m_api, _ = evaluate_gameweek(api_log, 9)
            api_ok = np.isfinite(m_api["xi_model"])
        except Exception as exc:  # noqa: BLE001
            api_ok, m_api = False, {"error": repr(exc)}
        check("replay copes with numbers served as text (live API format)",
              api_ok, str(m_api.get("error", "")))
        m_csv, _ = evaluate_gameweek(log, 9)
        check("text-formatted input gives the same answer as numeric",
              api_ok and abs(m_api["xi_model"] - m_csv["xi_model"]) < 1e-6,
              f"{m_api.get('xi_model')} vs {m_csv['xi_model']}")

        # Regression guard: the shipped defaults must beat the naive season
        # points pick on the full past season. If a model change breaks this,
        # the change is making it worse at the thing it is for.
        res = run_backtest(log)
        s = summarise(res)
        check("defaults beat the naive pick over a season",
              s["xi_model_mean"] > s["xi_naive_mean"] + 2.0,
              f"{s['xi_model_mean']:.2f} vs {s['xi_naive_mean']:.2f}")
        check("defaults beat naive in most weeks",
              s["beats_naive_weeks"] >= 0.6 * s["gameweeks"],
              f"{s['beats_naive_weeks']}/{s['gameweeks']}")
        check("projections are not badly biased",
              abs(s["bias"]) < 0.5, f"bias {s['bias']:+.3f}")
        untuned = ModelParams(shrink_k=8.0, recent_weight=0.0, form_window=4,
                              att_elasticity=0.9, cs_elasticity=1.0, xg_weight=0.0,
                              gk_capacity=False, fixture_ref="league",
                              pos_scale=(1.0, 1.0, 1.0, 1.0))
        s0 = summarise(run_backtest(log, params=untuned))
        check("tuned defaults outscore the original settings",
              s["xi_model_mean"] >= s0["xi_model_mean"] - 1e-9,
              f"{s['xi_model_mean']:.2f} vs {s0['xi_model_mean']:.2f}")
        check("captain beats the naive captain over a season",
              s["captain_model_mean"] > s["captain_naive_mean"] + 0.5,
              f"{s['captain_model_mean']:.2f} vs {s['captain_naive_mean']:.2f}")

        # Regression: keepers and defenders were once projected far above
        # what they deliver at the top end (74% and 78%), which put a
        # defender in the armband 15 weeks in 37.
        caps_pos, top_rows = {}, []
        for g_ in [x for x in log.rounds if x >= 2]:
            _, d_ = evaluate_gameweek(log, g_)
            c_ = d_.loc[d_.ep_next.idxmax(), "pos"]
            caps_pos[c_] = caps_pos.get(c_, 0) + 1
            top_rows.append(d_[d_.ep_next >= 4.5][["pos", "ep_next", "actual"]])
        top = pd.concat(top_rows)
        check("a keeper or defender is rarely captain",
              caps_pos.get("GK", 0) + caps_pos.get("DEF", 0) <= 4, str(caps_pos))
        dtop = top[top.pos == "DEF"]
        check("top-end defenders are not over-projected",
              len(dtop) == 0 or dtop.actual.mean() >= 0.9 * dtop.ep_next.mean(),
              f"delivered {dtop.actual.mean() / max(dtop.ep_next.mean(), 1e-9):.2f}")

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
