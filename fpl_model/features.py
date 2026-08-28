"""Turn raw FPL payloads into the tables the projection model needs.

Two things happen here:

1. Every upcoming fixture is converted into a pair of expected-goals numbers
   (how many the home side should score, how many the away side should) using
   FPL's published team strength ratings, a home-advantage term, and the
   official fixture difficulty rating as a sanity blend.
2. Every player is reduced to a per-90 scoring rate plus a breakdown of where
   those points come from (attacking / clean sheets / everything else), which
   is what lets the model react to fixtures differently by position.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------- #
# Tunable constants. These are the only "opinions" in the model - they are
# gathered here so you can argue with them in one place.
# ---------------------------------------------------------------------- #

BASE_HOME_GOALS = 1.55      # league-average goals for a home side
BASE_AWAY_GOALS = 1.25      # league-average goals for an away side
STRENGTH_EXPONENT = 1.00    # how hard team strength ratings bite
FDR_BLEND = 0.30            # weight on FPL's own 1-5 difficulty vs strengths
XG_FLOOR, XG_CEIL = 0.35, 3.6

POS_NAME = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_POINTS = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}
CS_POINTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}

# Fallback split of a player's points into fixture-sensitive buckets, used
# when a player has no usable history (new signings, promoted clubs).
DEFAULT_SHARES = {
    #        attacking, clean sheet, fixture-neutral
    "GK":  (0.02, 0.42, 0.56),
    "DEF": (0.22, 0.38, 0.40),
    "MID": (0.55, 0.08, 0.37),
    "FWD": (0.66, 0.00, 0.34),
}

# Points per 90 a "replacement level" player of each position produces.
REPLACEMENT_P90 = {"GK": 2.6, "DEF": 2.6, "MID": 2.6, "FWD": 2.6}


@dataclass
class Season:
    """Everything the projection model reads from."""
    players: pd.DataFrame
    teams: pd.DataFrame
    fixtures: pd.DataFrame
    events: pd.DataFrame
    next_gw: int
    current_gw: int | None
    gws_played: int
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------- #
# teams & fixtures
# ---------------------------------------------------------------------- #

STRENGTH_SPREAD = 0.17      # how far one standard deviation moves a team
STRENGTH_REL_BOUNDS = (0.72, 1.35)


def _usable(series: pd.Series) -> bool:
    """True only if a strength column carries real, varying information.

    FPL ships these columns as zeros until it has decided the season's
    ratings, and publishes them on different scales in different years
    (four-figure numbers one season, a 1-5 rating the next), so the test is
    "does this vary at all", not "is this a particular number".
    """
    s = pd.to_numeric(series, errors="coerce")
    return bool(s.notna().all() and (s > 0).all() and s.nunique() > 1)


def _relative(series: pd.Series) -> pd.Series:
    """Turn any strength scale into a multiplier centred on 1.0.

    Standardising first makes this scale-invariant, so it behaves the same
    whether FPL publishes 1340 or 4.
    """
    s = pd.to_numeric(series, errors="coerce").astype(float)
    std = s.std(ddof=0)
    if not np.isfinite(std) or std <= 0:
        return pd.Series(1.0, index=s.index)
    z = (s - s.mean()) / std
    return np.exp(STRENGTH_SPREAD * z).clip(*STRENGTH_REL_BOUNDS)


def build_teams(bootstrap: dict) -> pd.DataFrame:
    """Team ratings, with a documented fallback when FPL hasn't set them.

    Also returns, on the frame's `attrs`, which source was used and how much
    the fixture model should therefore lean on FPL's own difficulty ratings.
    """
    teams = pd.DataFrame(bootstrap["teams"])
    keep = ["id", "name", "short_name", "strength",
            "strength_attack_home", "strength_attack_away",
            "strength_defence_home", "strength_defence_away",
            "strength_overall_home", "strength_overall_away"]
    teams = teams[[c for c in keep if c in teams.columns]].copy()

    split_cols = ["strength_attack_home", "strength_attack_away",
                  "strength_defence_home", "strength_defence_away"]
    overall_cols = ["strength_overall_home", "strength_overall_away"]

    have_split = all(c in teams.columns and _usable(teams[c]) for c in split_cols)
    have_overall = all(c in teams.columns and _usable(teams[c]) for c in overall_cols)

    if have_split:
        # Best case: separate attack and defence ratings, home and away.
        source, fdr_blend = "attack/defence split", FDR_BLEND
        teams["att_home"] = _relative(teams.strength_attack_home)
        teams["att_away"] = _relative(teams.strength_attack_away)
        teams["def_home"] = _relative(teams.strength_defence_home)
        teams["def_away"] = _relative(teams.strength_defence_away)
    elif have_overall:
        # Pre-season FPL often publishes only an overall rating. A stronger
        # side both scores more and concedes less, so it drives both axes -
        # but it is coarser, so we lean harder on the difficulty ratings.
        source, fdr_blend = "overall rating only", 0.50
        home = _relative(teams.strength_overall_home)
        away = _relative(teams.strength_overall_away)
        teams["att_home"], teams["def_home"] = home, home
        teams["att_away"], teams["def_away"] = away, away
    else:
        # Nothing usable. Fall back entirely on FPL's fixture difficulty,
        # which is populated well before the strength ratings are.
        source, fdr_blend = "fixture difficulty only", 1.00
        for col in ("att_home", "att_away", "def_home", "def_away"):
            teams[col] = 1.0

    teams = teams.set_index("id")
    teams.attrs["strength_source"] = source
    teams.attrs["fdr_blend"] = fdr_blend
    return teams


def _xg_pair(att_rel: float, def_rel: float, base: float) -> float:
    """Expected goals for a side, from its attack vs the opponent's defence."""
    ratio = max(att_rel, 1e-6) / max(def_rel, 1e-6)
    return float(np.clip(base * ratio ** STRENGTH_EXPONENT, XG_FLOOR, XG_CEIL))


def _fdr_multiplier(fdr: float) -> float:
    """FPL's 1 (easiest) to 5 (hardest) rating as an attacking multiplier."""
    return float({1: 1.30, 2: 1.15, 3: 1.00, 4: 0.86, 5: 0.72}.get(int(fdr), 1.0))


def build_fixtures(fixtures_json: list[dict], teams: pd.DataFrame,
                   next_gw: int, horizon: int) -> pd.DataFrame:
    """One row per team per upcoming fixture, with expected goals both ways."""
    fdr_blend = float(teams.attrs.get("fdr_blend", FDR_BLEND))
    rows = []
    last_gw = next_gw + horizon - 1
    for fx in fixtures_json:
        gw = fx.get("event")
        if gw is None or gw < next_gw or gw > last_gw or fx.get("finished"):
            continue
        h, a = fx["team_h"], fx["team_a"]
        if h not in teams.index or a not in teams.index:
            continue
        th, ta = teams.loc[h], teams.loc[a]

        xg_h = _xg_pair(th.att_home, ta.def_away, BASE_HOME_GOALS)
        xg_a = _xg_pair(ta.att_away, th.def_home, BASE_AWAY_GOALS)

        # Blend in FPL's own difficulty rating so we are not fully hostage to
        # strength ratings that can be stale in pre-season.
        fdr_h = fx.get("team_h_difficulty") or 3
        fdr_a = fx.get("team_a_difficulty") or 3
        xg_h = xg_h * (1 - fdr_blend) + BASE_HOME_GOALS * _fdr_multiplier(fdr_h) * fdr_blend
        xg_a = xg_a * (1 - fdr_blend) + BASE_AWAY_GOALS * _fdr_multiplier(fdr_a) * fdr_blend

        for team_id, opp_id, home, xg_for, xg_against, fdr in (
            (h, a, True, xg_h, xg_a, fdr_h),
            (a, h, False, xg_a, xg_h, fdr_a),
        ):
            rows.append({
                "fixture_id": fx["id"], "gw": int(gw), "team": team_id,
                "opponent": opp_id, "is_home": home,
                "xg_for": xg_for, "xg_against": xg_against,
                "fdr": int(fdr),
                "kickoff": fx.get("kickoff_time"),
            })

    fixtures = pd.DataFrame(rows)
    if fixtures.empty:
        return fixtures
    bad = fixtures.xg_for.isna() | fixtures.xg_against.isna() | (fixtures.xg_for <= 0)
    if bad.any():
        raise ValueError(
            f"{int(bad.sum())} fixtures produced an invalid expected-goals "
            "figure - team strength ratings are unusable and the fallback "
            "did not catch it. Please report this.")
    fixtures["p_clean_sheet"] = np.exp(-fixtures.xg_against)
    fixtures["opp_short"] = fixtures.opponent.map(teams.short_name)
    fixtures = fixtures.sort_values(["gw", "team"]).reset_index(drop=True)
    return fixtures


def expected_conceded_penalty(xga: float) -> float:
    """E[-floor(goals conceded / 2)] under a Poisson goals model."""
    total = 0.0
    for k in range(0, 10):
        p = math.exp(-xga) * xga ** k / math.factorial(k)
        total += p * (k // 2)
    return -total


# ---------------------------------------------------------------------- #
# players
# ---------------------------------------------------------------------- #

def _season_weight(season_name: str, seasons_sorted: list[str]) -> float:
    """Most recent prior season counts fully, the one before it half."""
    try:
        idx = seasons_sorted.index(season_name)
    except ValueError:
        return 0.0
    return [1.0, 0.5, 0.2][idx] if idx < 3 else 0.0


def _history_totals(summary: dict) -> dict:
    """Weighted sum of a player's previous Premier League seasons."""
    past = summary.get("history_past", []) or []
    past = [s for s in past if s.get("minutes", 0) > 0]
    if not past:
        return {}
    names = sorted({s["season_name"] for s in past}, reverse=True)
    fields = ["minutes", "total_points", "goals_scored", "assists",
              "clean_sheets", "goals_conceded", "saves", "bonus", "bps",
              "starts", "yellow_cards", "red_cards", "penalties_saved",
              "expected_goals", "expected_assists", "defensive_contribution"]
    agg = {f: 0.0 for f in fields}
    agg["weight"] = 0.0
    for s in past:
        w = _season_weight(s["season_name"], names)
        if w <= 0:
            continue
        agg["weight"] += w
        for f in fields:
            try:
                agg[f] += w * float(s.get(f) or 0)
            except (TypeError, ValueError):
                pass
    if agg["weight"] <= 0 or agg["minutes"] <= 0:
        return {}
    # Normalise back to a single-season equivalent so minutes, starts and
    # points stay on the same scale as this season's totals.
    w_total = agg["weight"]
    for f in fields:
        agg[f] /= w_total
    return agg


def _current_totals(row: pd.Series) -> dict:
    """This season's totals straight off the bootstrap payload."""
    def num(name):
        try:
            return float(row.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0
    return {
        "minutes": num("minutes"), "total_points": num("total_points"),
        "goals_scored": num("goals_scored"), "assists": num("assists"),
        "clean_sheets": num("clean_sheets"), "goals_conceded": num("goals_conceded"),
        "saves": num("saves"), "bonus": num("bonus"), "bps": num("bps"),
        "starts": num("starts"), "yellow_cards": num("yellow_cards"),
        "red_cards": num("red_cards"), "penalties_saved": num("penalties_saved"),
        "expected_goals": num("expected_goals"),
        "expected_assists": num("expected_assists"),
        "defensive_contribution": num("defensive_contribution"),
    }


def _point_shares(tot: dict, pos: str) -> tuple[float, float, float]:
    """Split a player's historical points into attack / clean sheet / neutral.

    Bonus is apportioned to attack and clean sheets in proportion to the rest,
    because bonus points follow whatever the player is actually good at.
    """
    pts = tot.get("total_points", 0.0)
    if pts <= 0 or tot.get("minutes", 0) < 270:
        return DEFAULT_SHARES[pos]

    attack = tot["goals_scored"] * GOAL_POINTS[pos] + tot["assists"] * 3.0
    clean = tot["clean_sheets"] * CS_POINTS[pos]
    bonus = tot.get("bonus", 0.0)
    core = attack + clean
    if core > 0:
        attack += bonus * (attack / core)
        clean += bonus * (clean / core)
    else:
        # Defensive-contribution merchants and shot-stoppers: bonus is neutral.
        pass

    attack = max(attack, 0.0)
    clean = max(clean, 0.0)
    neutral = max(pts - attack - clean, 0.0)
    total = attack + clean + neutral
    if total <= 0:
        return DEFAULT_SHARES[pos]

    shares = (attack / total, clean / total, neutral / total)
    # Blend a little toward the positional default so one freak season does
    # not produce a 95%-attacking defender.
    d = DEFAULT_SHARES[pos]
    k = 0.75
    return tuple(k * s + (1 - k) * dv for s, dv in zip(shares, d))


def _availability(row: pd.Series) -> tuple[float, str]:
    """Probability the player is fit to feature, plus a human-readable flag."""
    status = str(row.get("status") or "a")
    news = str(row.get("news") or "").strip()
    if news.lower() in ("nan", "none"):
        news = ""

    raw = row.get("chance_of_playing_next_round")
    try:
        chance = float(raw)
        if not np.isfinite(chance):
            chance = None
    except (TypeError, ValueError):
        chance = None

    # FPL leaves the chance field empty for anyone with no doubt over them, so
    # a missing value means different things depending on the status flag.
    defaults = {"a": 1.0, "d": 0.5, "i": 0.0, "s": 0.0, "u": 0.0, "n": 0.0}
    p = defaults.get(status, 1.0) if chance is None else chance / 100.0
    return float(np.clip(p, 0.0, 1.0)), news


def build_players(bootstrap: dict, summaries: dict[int, dict],
                  teams: pd.DataFrame, gws_played: int) -> pd.DataFrame:
    """One row per player with a per-90 rate, minutes model and points split."""
    el = pd.DataFrame(bootstrap["elements"])
    el["pos"] = el.element_type.map(POS_NAME)
    el["price"] = el.now_cost / 10.0
    el["team_name"] = el.team.map(teams.name)
    el["team_short"] = el.team.map(teams.short_name)
    el["name"] = el.web_name
    el["full_name"] = (el.first_name.fillna("") + " " + el.second_name.fillna("")).str.strip()

    # How many matches this season's totals actually cover. Normally that is
    # the number of finished gameweeks, but the API keeps last season's totals
    # on display until the new season kicks off, so infer it from the data
    # rather than trusting the calendar.
    played_minutes = el.minutes.fillna(0)
    implied = int(np.ceil(np.percentile(played_minutes, 99) / 90.0)) if len(el) else 0
    games_ref = max(gws_played, implied, 1)

    records = []
    for _, row in el.iterrows():
        pid = int(row.id)
        pos = row.pos
        curr = _current_totals(row)
        hist = _history_totals(summaries.get(pid, {}))

        # --- how much do we trust this season vs last? ------------------
        # Current-season evidence takes over gradually; ~10 full matches of
        # minutes and it dominates.
        curr_min = curr["minutes"]
        w_curr = float(np.clip(curr_min / 900.0, 0.0, 1.0))
        has_hist = bool(hist) and hist.get("minutes", 0) > 0
        if not has_hist:
            w_curr = 1.0 if curr_min > 0 else 0.0

        def blend(field: str) -> float:
            c = curr.get(field, 0.0)
            h = hist.get(field, 0.0) if has_hist else 0.0
            return w_curr * c + (1 - w_curr) * h

        blended_minutes = blend("minutes")
        blended_points = blend("total_points")

        # --- raw scoring rate, shrunk toward replacement level -----------
        n90 = blended_minutes / 90.0
        prior = REPLACEMENT_P90[pos]
        k = 8.0  # equivalent to 8 full matches of prior evidence
        raw_p90 = (blended_points / n90) if n90 > 0 else prior
        base_p90 = (n90 * raw_p90 + k * prior) / (n90 + k)

        # --- recent form nudge ------------------------------------------
        try:
            form = float(row.get("form") or 0)
        except (TypeError, ValueError):
            form = 0.0
        form_weight = float(np.clip(gws_played / 6.0, 0.0, 1.0)) * 0.35
        if gws_played > 0 and form > 0:
            base_p90 = (1 - form_weight) * base_p90 + form_weight * form

        # --- minutes model ----------------------------------------------
        # Start probability from starts per available match, blended the same
        # way; minutes when starting from the historical average.
        starts_rate_c = curr["starts"] / games_ref
        starts_rate_h = hist.get("starts", 0.0) / 38.0 if has_hist else 0.0
        p_start = w_curr * starts_rate_c + (1 - w_curr) * starts_rate_h
        p_start = float(np.clip(p_start, 0.0, 0.97))

        mins_per_start = 78.0
        starts_total = blend("starts")
        if blended_minutes > 0 and starts_total >= 3:
            mins_per_start = float(np.clip(blended_minutes / starts_total, 45, 90))

        p_sub = float(np.clip(0.35 * (1 - p_start), 0.0, 0.45))
        xmins = p_start * mins_per_start + p_sub * 18.0
        p_app = float(np.clip(p_start + p_sub, 0.0, 1.0))
        p60 = float(np.clip(p_start * (0.90 if mins_per_start > 70 else 0.65), 0.0, 0.97))

        avail, news = _availability(row)

        att_share, cs_share, neutral_share = _point_shares(
            hist if has_hist and w_curr < 0.6 else
            ({kk: w_curr * curr.get(kk, 0) + (1 - w_curr) * hist.get(kk, 0)
              for kk in set(curr) | set(hist)} if has_hist else curr),
            pos)

        records.append({
            "id": pid,
            "base_p90": base_p90,
            "xmins": xmins, "p_app": p_app, "p60": p60, "p_start": p_start,
            "availability": avail, "news": news,
            "att_share": att_share, "cs_share": cs_share,
            "neutral_share": neutral_share,
            "hist_minutes": hist.get("minutes", 0.0) if has_hist else 0.0,
            "hist_points": hist.get("total_points", 0.0) if has_hist else 0.0,
            "curr_minutes": curr_min, "curr_points": curr["total_points"],
            "w_curr": w_curr,
            "has_history": has_hist,
        })

    feats = pd.DataFrame(records).set_index("id")
    base = el.set_index("id")
    out = base.drop(columns=[c for c in feats.columns if c in base.columns]).join(feats)

    # Price-aware prior: FPL's own pricing carries information about expected
    # output, especially for players with no Premier League history at all.
    out = _apply_price_prior(out)
    return out.reset_index()


def _apply_price_prior(players: pd.DataFrame) -> pd.DataFrame:
    """Pull players with thin evidence toward what their price implies.

    Fitted on the players who *do* have a track record, so the relationship is
    learned from this season's actual pricing rather than hard-coded.
    """
    players = players.copy()
    players["price_prior_p90"] = np.nan

    for pos, grp in players.groupby("pos"):
        trained = grp[(grp.hist_minutes + grp.curr_minutes) >= 900]
        if len(trained) >= 12:
            x = np.log(trained.price.values)
            y = trained.base_p90.values
            slope, intercept = np.polyfit(x, y, 1)
            pred = intercept + slope * np.log(grp.price.values)
        else:
            pred = np.full(len(grp), REPLACEMENT_P90[pos])
        lo, hi = REPLACEMENT_P90[pos] * 0.6, 9.0
        players.loc[grp.index, "price_prior_p90"] = np.clip(pred, lo, hi)

    evidence = (players.hist_minutes + players.curr_minutes) / 90.0
    w_evidence = np.clip(evidence / (evidence + 10.0), 0.0, 1.0)
    players["base_p90"] = (w_evidence * players.base_p90
                           + (1 - w_evidence) * players.price_prior_p90)

    # Players with no track record at all still need a minutes estimate; use
    # price rank within their own club and position as a proxy for pecking order.
    blank = players.hist_minutes + players.curr_minutes < 90
    if blank.any():
        rank = (players[blank].groupby(["team", "pos"]).price
                .rank(ascending=False, method="min"))
        implied_start = rank.map({1: 0.72, 2: 0.42, 3: 0.20}).fillna(0.08)
        players.loc[blank, "p_start"] = implied_start
        players.loc[blank, "xmins"] = implied_start * 76.0 + 0.2 * 18.0
        players.loc[blank, "p60"] = implied_start * 0.85
        players.loc[blank, "p_app"] = np.clip(implied_start + 0.2, 0, 1)
    return players


def parse_events(bootstrap: dict) -> tuple[pd.DataFrame, int, int | None, int]:
    events = pd.DataFrame(bootstrap["events"])
    finished = events[events.finished == True]  # noqa: E712
    gws_played = int(len(finished))

    current = events[events.is_current == True]  # noqa: E712
    current_gw = int(current.id.iloc[0]) if len(current) else None

    nxt = events[events.is_next == True]  # noqa: E712
    if len(nxt):
        next_gw = int(nxt.id.iloc[0])
    elif current_gw is not None:
        next_gw = min(current_gw + 1, int(events.id.max()))
    else:
        next_gw = int(events.id.min())
    return events, next_gw, current_gw, gws_played
