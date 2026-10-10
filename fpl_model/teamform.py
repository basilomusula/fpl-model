"""Team form: attack and defence ratings from this season's results.

FPL's strength ratings are set by FPL, change slowly, and in some seasons are
not published until well into the autumn. Results are the evidence they
lag behind. This module turns every finished match into two numbers per club
- how much it scored and how much it conceded, in expected goals blended
with actual goals - and fits a rating for each club's attack and defence:

* **opponent-adjusted**: three goals against the bottom side count for less
  than three against the champions;
* **recency-weighted**: a result's weight halves every `form_half_life`
  gameweeks, so the ratings follow a club's current level;
* **shrunk toward FPL's own ratings**: `form_k` games of "as FPL rated
  them" are added to every club, so after two games the ratings have barely
  moved and after twenty they are mostly results;
* **split home and away**: each club's home and away records get their own
  adjustment on top of the league-wide home advantage, shrunk harder
  (`form_venue_k`) because each split rests on half the games.

The result replaces the att/def columns of the teams table, which is all the
fixture model reads, so everything downstream - expected goals, clean-sheet
odds, every player's projection - follows automatically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BASE_HOME, BASE_AWAY = 1.55, 1.25       # league-average goals by venue
RATING_BOUNDS = (0.55, 1.80)


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if np.isfinite(v) else float("nan")


def match_log(fixtures_json: list[dict], summaries: dict[int, dict] | None = None
              ) -> pd.DataFrame:
    """One row per club per finished match: goals and expected goals each way.

    Expected goals come from summing the players' per-match figures. They
    are only used when the player histories account for nearly every goal
    scored (otherwise some players' histories are missing and team xG would
    be understated), and actual goals are used alone.
    """
    played = [f for f in fixtures_json
              if f.get("finished") and f.get("event") is not None
              and f.get("team_h_score") is not None and f.get("team_a_score") is not None]
    if not played:
        return pd.DataFrame(columns=["fixture", "gw", "team", "opp", "home",
                                     "gf", "ga", "xgf", "xga"])
    side_xg: dict[tuple[int, bool], float] = {}
    side_goals: dict[tuple[int, bool], float] = {}
    for s in (summaries or {}).values():
        for h in s.get("history", []) or []:
            fid = h.get("fixture")
            if fid is None:
                continue
            key = (int(fid), bool(h.get("was_home")))
            xg = _num(h.get("expected_goals"))
            if np.isfinite(xg):
                side_xg[key] = side_xg.get(key, 0.0) + xg
            gs = _num(h.get("goals_scored"))
            if np.isfinite(gs):
                side_goals[key] = side_goals.get(key, 0.0) + gs

    rows = []
    goals_total, goals_found = 0.0, 0.0
    for f in played:
        fid, gw = int(f["id"]), int(f["event"])
        hs, as_ = _num(f["team_h_score"]), _num(f["team_a_score"])
        goals_total += hs + as_
        goals_found += side_goals.get((fid, True), 0.0) + side_goals.get((fid, False), 0.0)
        xh, xa = side_xg.get((fid, True), np.nan), side_xg.get((fid, False), np.nan)
        rows.append({"fixture": fid, "gw": gw, "team": int(f["team_h"]),
                     "opp": int(f["team_a"]), "home": True,
                     "gf": hs, "ga": as_, "xgf": xh, "xga": xa})
        rows.append({"fixture": fid, "gw": gw, "team": int(f["team_a"]),
                     "opp": int(f["team_h"]), "home": False,
                     "gf": as_, "ga": hs, "xgf": xa, "xga": xh})
    log = pd.DataFrame(rows)
    # Own goals are credited to nobody, so a complete set of histories still
    # finds a few percent fewer goals than were scored.
    if goals_total <= 0 or goals_found < 0.9 * goals_total:
        log["xgf"] = np.nan
        log["xga"] = np.nan
    return log


def _blend(log: pd.DataFrame, xg_weight: float) -> tuple[pd.Series, pd.Series]:
    have = log.xgf.notna() & log.xga.notna()
    w = xg_weight if bool(have.all()) else 0.0
    yf = w * log.xgf.fillna(log.gf) + (1 - w) * log.gf
    ya = w * log.xga.fillna(log.ga) + (1 - w) * log.ga
    return yf, ya


def form_ratings(log: pd.DataFrame, prior: pd.DataFrame, next_gw: int,
                 params) -> pd.DataFrame:
    """Fit attack/defence ratings per club and venue, shrunk toward `prior`.

    `prior` is indexed by team id with att_home, att_away, def_home and
    def_away as FPL-derived multipliers around 1.0 (higher def = tighter).
    Returns the same four columns plus the evidence behind them.
    """
    teams = list(prior.index)
    p_att = np.sqrt(prior.att_home * prior.att_away).astype(float)
    p_con = 1.0 / np.sqrt(prior.def_home * prior.def_away).astype(float)
    out = prior[["att_home", "att_away", "def_home", "def_away"]].astype(float).copy()
    out["games"] = 0
    if log.empty:
        return out

    lg = log[log.team.isin(teams) & log.opp.isin(teams)].copy()
    lg["yf"], lg["ya"] = _blend(lg, params.form_xg_weight)
    lg["base_f"] = np.where(lg.home, BASE_HOME, BASE_AWAY)
    lg["base_a"] = np.where(lg.home, BASE_AWAY, BASE_HOME)
    age = (next_gw - 1 - lg.gw).clip(lower=0)
    lg["w"] = 0.5 ** (age / max(params.form_half_life, 1e-6))
    k = max(params.form_k, 0.0)
    unit = (BASE_HOME + BASE_AWAY) / 2

    att, con = p_att.copy(), p_con.copy()
    for _ in range(8):
        # Attack: goals scored relative to what the opponent usually concedes.
        exp_f = lg.base_f * lg.opp.map(con)
        num = (lg.w * lg.yf).groupby(lg.team).sum()
        den = (lg.w * exp_f).groupby(lg.team).sum()
        att = ((num.reindex(teams, fill_value=0) + k * unit * p_att)
               / (den.reindex(teams, fill_value=0) + k * unit))
        # Defence: goals conceded relative to what the opponent usually scores.
        exp_a = lg.base_a * lg.opp.map(att)
        num = (lg.w * lg.ya).groupby(lg.team).sum()
        den = (lg.w * exp_a).groupby(lg.team).sum()
        con = ((num.reindex(teams, fill_value=0) + k * unit * p_con)
               / (den.reindex(teams, fill_value=0) + k * unit))
        # Ratings are relative: keep the league average at 1.
        att = att / np.exp(np.log(att).mean())
        con = con / np.exp(np.log(con).mean())

    # Home and away: how each club did at each venue against what its overall
    # rating predicted, shrunk toward "no different" with form_venue_k games.
    kv = max(params.form_venue_k, 0.0)
    lg["exp_f"] = lg.base_f * lg.team.map(att) * lg.opp.map(con)
    lg["exp_a"] = lg.base_a * lg.opp.map(att) * lg.team.map(con)

    def venue(home: bool, y: str, e: str) -> pd.Series:
        sub = lg[lg.home == home]
        num = (sub.w * sub[y]).groupby(sub.team).sum().reindex(teams, fill_value=0)
        den = (sub.w * sub[e]).groupby(sub.team).sum().reindex(teams, fill_value=0)
        return (num + kv * unit) / (den + kv * unit)

    # The prior's own home/away tilt (FPL rates some clubs stronger at home)
    # is kept, scaled by how far form has moved the club overall.
    tilt_att_h = prior.att_home / np.sqrt(prior.att_home * prior.att_away)
    tilt_att_a = prior.att_away / np.sqrt(prior.att_home * prior.att_away)
    tilt_def_h = prior.def_home / np.sqrt(prior.def_home * prior.def_away)
    tilt_def_a = prior.def_away / np.sqrt(prior.def_home * prior.def_away)
    out["att_home"] = att * tilt_att_h * venue(True, "yf", "exp_f")
    out["att_away"] = att * tilt_att_a * venue(False, "yf", "exp_f")
    out["def_home"] = tilt_def_h / (con * venue(True, "ya", "exp_a"))
    out["def_away"] = tilt_def_a / (con * venue(False, "ya", "exp_a"))
    for c in ("att_home", "att_away", "def_home", "def_away"):
        out[c] = out[c].clip(*RATING_BOUNDS)
    out["games"] = lg.groupby("team").size().reindex(teams, fill_value=0).astype(int)
    return out


def form_table(log: pd.DataFrame, teams: pd.DataFrame, rated: pd.DataFrame,
               last_n: int = 5) -> list[dict]:
    """A per-club summary for the dashboard: results, venue splits, ratings."""
    rows = []
    if log.empty:
        return rows
    lg = log.sort_values(["gw", "fixture"])
    for tid, grp in lg.groupby("team"):
        if tid not in teams.index:
            continue
        res = ["W" if r.gf > r.ga else "L" if r.gf < r.ga else "D"
               for r in grp.itertuples()]
        pts = sum(3 if x == "W" else 1 if x == "D" else 0 for x in res)
        h, a = grp[grp.home], grp[~grp.home]
        recent = grp.tail(last_n)

        def per(df: pd.DataFrame, col: str) -> float | None:
            return round(float(df[col].mean()), 2) if len(df) and df[col].notna().all() else None

        r = rated.loc[tid] if tid in rated.index else None
        rows.append({
            "team": int(tid), "short": str(teams.loc[tid, "short_name"]),
            "played": int(len(grp)), "ppg": round(pts / max(len(grp), 1), 2),
            "last": "".join(res[-last_n:]),
            "last_ppg": round(sum(3 if x == "W" else 1 if x == "D" else 0
                                  for x in res[-last_n:]) / max(len(recent), 1), 2),
            "home_gf": per(h, "gf"), "home_ga": per(h, "ga"),
            "away_gf": per(a, "gf"), "away_ga": per(a, "ga"),
            "home_xgf": per(h, "xgf"), "home_xga": per(h, "xga"),
            "away_xgf": per(a, "xgf"), "away_xga": per(a, "xga"),
            "att": None if r is None else round(float(np.sqrt(r.att_home * r.att_away)), 2),
            "def": None if r is None else round(float(np.sqrt(r.def_home * r.def_away)), 2),
        })
    return sorted(rows, key=lambda x: (-x["ppg"], x["short"]))


def apply_team_form(teams: pd.DataFrame, fixtures_json: list[dict],
                    summaries: dict[int, dict] | None, next_gw: int,
                    params) -> pd.DataFrame:
    """Teams table with att/def columns updated from this season's results.

    A no-op (apart from attaching the form table) when params.team_form is
    off, so the dashboard can still show form either way.
    """
    log = match_log(fixtures_json, summaries)
    out = teams.copy()
    out.attrs.update(teams.attrs)
    rated = form_ratings(log, teams, next_gw, params) if params.team_form else teams
    if params.team_form and not log.empty:
        for c in ("att_home", "att_away", "def_home", "def_away"):
            out[c] = rated[c]
    out.attrs["form_table"] = form_table(log, teams, out)
    out.attrs["form_games"] = int(len(log) // 2)
    out.attrs["form_uses_xg"] = bool(len(log) and log.xgf.notna().all())
    return out
