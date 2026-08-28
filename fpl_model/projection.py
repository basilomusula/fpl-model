"""Expected points per player per gameweek.

The model is deliberately two-layer:

  * a **baseline rate** (points per 90) learned from what the player has
    actually done, shrunk toward a price-implied prior when the evidence is
    thin, and
  * a **fixture adjustment** that scales only the parts of that baseline which
    a fixture can plausibly move - attacking returns scale with how many goals
    the team is expected to score, clean-sheet points scale with how likely a
    shutout is, and appearance points, cards and defensive-contribution points
    are left alone.

Doing it this way means bonus points, defensive contributions and every other
quirk of the scoring system are already inside the baseline; we never have to
guess a coefficient for them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import Season, expected_conceded_penalty

ATT_ELASTICITY = 0.90       # attacking returns vs team expected goals
CS_ELASTICITY = 1.00        # clean-sheet points vs shutout probability
ATT_MULT_BOUNDS = (0.55, 1.70)
CS_MULT_BOUNDS = (0.30, 2.30)
HORIZON_DECAY = 0.88        # a point in GW+4 is worth less than one in GW+1


def project(season: Season, horizon: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (per-gameweek projections, per-player summary).

    The per-gameweek frame has one row per player per fixture, so a double
    gameweek naturally produces two rows and a blank produces none.
    """
    players, fixtures = season.players, season.fixtures
    if fixtures.empty:
        raise ValueError("no upcoming fixtures found - is the season over?")

    league_xg = float(fixtures.xg_for.mean())
    league_xga = float(fixtures.xg_against.mean())
    league_pcs = float(fixtures.p_clean_sheet.mean())

    fx = fixtures.copy()
    fx["att_mult"] = np.clip((fx.xg_for / league_xg) ** ATT_ELASTICITY,
                             *ATT_MULT_BOUNDS)
    fx["cs_mult"] = np.clip((fx.p_clean_sheet / league_pcs) ** CS_ELASTICITY,
                            *CS_MULT_BOUNDS)
    fx["conceded_delta"] = (fx.xg_against.map(expected_conceded_penalty)
                            - expected_conceded_penalty(league_xga))

    cols = ["id", "name", "full_name", "pos", "team", "team_short", "price",
            "base_p90", "xmins", "p60", "p_app", "p_start", "availability",
            "att_share", "cs_share", "neutral_share", "news", "status",
            "selected_by_percent", "has_history", "curr_minutes",
            "hist_minutes", "form", "total_points"]
    p = players[[c for c in cols if c in players.columns]].copy()

    merged = p.merge(fx, on="team", how="inner", suffixes=("", "_fx"))

    fixture_factor = (merged.att_share * merged.att_mult
                      + merged.cs_share * merged.cs_mult
                      + merged.neutral_share)
    ep = merged.base_p90 * fixture_factor * (merged.xmins / 90.0)

    # Goals-conceded deductions only exist for goalkeepers and defenders and
    # only while they are on the pitch.
    is_back = merged.pos.isin(["GK", "DEF"])
    ep = ep + np.where(is_back, merged.conceded_delta * merged.p60, 0.0)

    ep = pd.to_numeric(pd.Series(ep, index=merged.index), errors="coerce")

    # A silent collapse to zero is the failure mode that looks like a working
    # model but ranks players by nothing at all, so refuse to continue.
    if not np.isfinite(ep).any() or float(ep.max(skipna=True) or 0) <= 0:
        raise ValueError(
            "every projection came out empty. This usually means the team "
            "strength ratings or fixture difficulties could not be read from "
            "the FPL API. Try --refresh, and report it if it persists.")
    bad = int(ep.isna().sum())
    if bad > 0.05 * len(ep):
        raise ValueError(f"{bad} of {len(ep)} projections are undefined - "
                         "the input data looks incomplete, try --refresh")
    ep = ep.fillna(0.0).clip(lower=0.0)
    avail = pd.to_numeric(merged.availability, errors="coerce").fillna(1.0)
    merged["ep_raw"] = ep              # ignoring injury and suspension flags
    merged["ep"] = ep * avail
    merged["availability"] = avail

    per_gw = merged[[
        "id", "name", "pos", "team", "team_short", "price", "gw", "opponent",
        "opp_short", "is_home", "fdr", "xg_for", "xg_against", "p_clean_sheet",
        "att_mult", "cs_mult", "ep", "ep_raw", "availability", "xmins",
        "news", "status", "kickoff",
    ]].sort_values(["id", "gw"]).reset_index(drop=True)

    summary = _summarise(p, per_gw, season, horizon)
    return per_gw, summary


def _summarise(players: pd.DataFrame, per_gw: pd.DataFrame,
               season: Season, horizon: int) -> pd.DataFrame:
    """Collapse the per-gameweek view into one ranked row per player."""
    gws = sorted(per_gw.gw.unique())[:horizon]
    window = per_gw[per_gw.gw.isin(gws)]

    weights = {gw: HORIZON_DECAY ** i for i, gw in enumerate(gws)}
    window = window.assign(w=window.gw.map(weights))
    window = window.assign(ep_weighted=window.ep * window.w)

    agg = window.groupby("id").agg(
        ep_next=("ep", lambda s: float(s.iloc[:1].sum())),  # replaced below
        ep_horizon=("ep", "sum"),
        ep_weighted=("ep_weighted", "sum"),
        fixtures_n=("gw", "count"),
        mean_fdr=("fdr", "mean"),
        home_games=("is_home", "sum"),
    )

    first_gw = gws[0] if gws else season.next_gw
    ep_next = (per_gw[per_gw.gw == first_gw].groupby("id").ep.sum()
               .rename("ep_next"))
    agg = agg.drop(columns=["ep_next"]).join(ep_next).fillna({"ep_next": 0.0})

    out = players.set_index("id").join(agg, how="left")
    out[["ep_next", "ep_horizon", "ep_weighted"]] = (
        out[["ep_next", "ep_horizon", "ep_weighted"]].fillna(0.0))
    out["fixtures_n"] = out.fixtures_n.fillna(0).astype(int)
    out["value"] = out.ep_horizon / out.price          # points per million
    out["ep_per_game"] = np.where(out.fixtures_n > 0,
                                  out.ep_horizon / out.fixtures_n, 0.0)

    # The upcoming run, twice over: a compact string for the CSVs and a
    # structured version the dashboard renders as difficulty chips.
    runs, lists = {}, {}
    for pid, grp in per_gw[per_gw.gw.isin(gws)].groupby("id"):
        parts, items = [], []
        for _, r in grp.sort_values("gw").iterrows():
            tag = r.opp_short.upper() if r.is_home else r.opp_short.lower()
            parts.append(f"{tag}({'H' if r.is_home else 'A'},{r.fdr})")
            items.append({"gw": int(r.gw), "opp": str(r.opp_short),
                          "home": bool(r.is_home), "fdr": int(r.fdr),
                          "ep": round(float(r.ep), 2)})
        runs[pid] = " ".join(parts)
        lists[pid] = items
    out["fixture_run"] = out.index.map(runs).fillna("-")
    out["fixture_list"] = out.index.map(lambda i: lists.get(i, []))

    # Tie-break explicitly. FPL returns players grouped by club, so a column
    # with many ties would otherwise sort into club blocks and read as a bug.
    out = out.sort_values(["ep_horizon", "ep_next", "base_p90", "price"],
                          ascending=False, kind="mergesort")
    out["rank_overall"] = np.arange(1, len(out) + 1)
    out["rank_pos"] = out.groupby("pos").ep_horizon.rank(
        ascending=False, method="min").astype(int)
    return out.reset_index()


def gw_matrix(per_gw: pd.DataFrame, player_ids: list[int],
              gws: list[int]) -> dict[int, dict[int, float]]:
    """{player_id: {gw: expected points}} - doubles summed, blanks absent."""
    sub = per_gw[per_gw.id.isin(player_ids) & per_gw.gw.isin(gws)]
    grouped = sub.groupby(["id", "gw"]).ep.sum()
    out: dict[int, dict[int, float]] = {pid: {} for pid in player_ids}
    for (pid, gw), val in grouped.items():
        out[int(pid)][int(gw)] = float(val)
    return out
