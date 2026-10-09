"""Walk-forward backtest: how good were the projections, really?

For every completed gameweek the model is rebuilt using only what was knowable
before that deadline - the per-round log up to the previous gameweek, the
price at the time, the fixtures and their difficulty - then its projections
are scored against what the players actually did.

Nothing from the future leaks in. That matters: re-running today's model on
last month's fixtures with today's form and prices would "predict" that the
players we now know are good were good, which is flattering and useless.

The same harness runs on two data sources:

* a full past season from the public per-gameweek CSVs (for tuning), and
* this season's per-round log straight from the FPL API (for the live
  scorecard on the dashboard).

Known limitation: historic injury flags are not recorded anywhere, so the
backtest cannot know a player was ruled out on Friday. The live model does.
That makes the backtest slightly pessimistic about the live model, never
optimistic, which is the right way round.
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .features import (DEFAULT_PARAMS, ModelParams, Season, build_fixtures,
                       build_players, build_teams)
from .optimiser import best_xi_for_gw
from .projection import project

POS_CODE = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
POS_NAME = {v: k for k, v in POS_CODE.items()}

# Fields summed from the per-round log into "season so far" totals.
SUM_FIELDS = ["minutes", "total_points", "goals_scored", "assists", "clean_sheets",
              "goals_conceded", "saves", "bonus", "bps", "starts", "yellow_cards",
              "red_cards", "penalties_saved", "expected_goals", "expected_assists",
              "defensive_contribution", "own_goals", "penalties_missed"]


# ---------------------------------------------------------------------- #
# data
# ---------------------------------------------------------------------- #

@dataclass
class GameweekLog:
    """Everything needed to rebuild the model as of any gameweek."""
    rows: pd.DataFrame                  # one row per player per fixture played
    elements: pd.DataFrame              # id, code, web_name, element_type, team
    teams: list[dict]                   # bootstrap-style team dicts
    fixtures: list[dict]                # bootstrap-style fixture dicts
    history_past: dict[int, list[dict]] = field(default_factory=dict)  # by code
    label: str = ""

    def __post_init__(self) -> None:
        # The live API sends some numbers as text ("expected_goals": "0.45").
        # Summing text columns concatenates them ("0.450.12") instead of
        # adding, so every numeric field is coerced here, once, for both the
        # CSV and the API constructors.
        rows = self.rows.copy()
        for col in SUM_FIELDS + ["value", "round", "xP", "opponent_team"]:
            if col in rows.columns:
                rows[col] = pd.to_numeric(rows[col], errors="coerce")
        for col in SUM_FIELDS:
            if col in rows.columns:
                rows[col] = rows[col].fillna(0.0)
        self.rows = rows

    @property
    def rounds(self) -> list[int]:
        return sorted(int(r) for r in self.rows["round"].dropna().unique())

    # ------------------------------------------------------------------ #
    @classmethod
    def from_csvs(cls, merged_gw: str, players_raw: str, teams_csv: str,
                  fixtures_csv: str, prior_players_csvs: dict[str, str] | None = None,
                  label: str = "") -> "GameweekLog":
        """Build from the per-gameweek CSVs published by the vaastav repo."""
        g = pd.read_csv(merged_gw, low_memory=False)
        pr = pd.read_csv(players_raw)
        teams = pd.read_csv(teams_csv)
        fx = pd.read_csv(fixtures_csv)

        name_to_id = dict(zip(teams.name, teams.id))
        g = g.rename(columns={"element": "id"})
        g["team_id"] = g.team.map(name_to_id)
        g["element_type"] = g.position.map(POS_CODE)
        for col in SUM_FIELDS + ["value", "xP", "was_home", "opponent_team", "kickoff_time"]:
            if col not in g.columns:
                g[col] = 0 if col not in ("kickoff_time",) else ""

        meta = (pr[["id", "code", "web_name", "first_name", "second_name",
                    "element_type", "team"]].copy())
        # Players who moved clubs mid-season: trust the log's latest team.
        latest_team = (g.sort_values("round").groupby("id").team_id.last())
        meta["team"] = meta.id.map(latest_team).fillna(meta.team).astype(int)

        fx = fx.where(pd.notnull(fx), None)
        fixtures = fx.to_dict("records")
        for f in fixtures:
            f["finished"] = bool(f.get("finished", False))

        past: dict[int, list[dict]] = {}
        for season_name, path in (prior_players_csvs or {}).items():
            prev = pd.read_csv(path)
            for _, r in prev.iterrows():
                rec = {"season_name": season_name}
                for f_ in SUM_FIELDS:
                    rec[f_] = float(r[f_]) if f_ in prev.columns and pd.notnull(r[f_]) else 0.0
                past.setdefault(int(r["code"]), []).append(rec)

        return cls(rows=g, elements=meta, teams=teams.where(pd.notnull(teams), None)
                   .to_dict("records"), fixtures=fixtures, history_past=past,
                   label=label)

    # ------------------------------------------------------------------ #
    @classmethod
    def from_api(cls, bootstrap: dict, fixtures: list[dict],
                 summaries: dict[int, dict], label: str = "") -> "GameweekLog":
        """Build from live bootstrap + element-summary payloads."""
        el = pd.DataFrame(bootstrap["elements"])
        meta = el[["id", "code", "web_name", "first_name", "second_name",
                   "element_type", "team"]].copy()
        recs = []
        for pid, s in summaries.items():
            for h in s.get("history", []) or []:
                row = {k: h.get(k) for k in SUM_FIELDS + ["value", "was_home",
                                                         "opponent_team", "kickoff_time"]}
                row["id"] = int(pid)
                row["round"] = h.get("round")
                row["xP"] = np.nan
                recs.append(row)
        rows = pd.DataFrame(recs)
        if rows.empty:
            rows = pd.DataFrame(columns=["id", "round"] + SUM_FIELDS + ["value"])
        rows = rows.merge(meta[["id", "element_type", "team"]].rename(
            columns={"team": "team_id"}), on="id", how="left")
        # A log without prices (some caches) falls back to today's price.
        if "value" not in rows.columns or rows.value.isna().all():
            rows["value"] = rows.id.map(dict(zip(el.id, el.now_cost)))
        else:
            rows["value"] = rows.value.fillna(rows.id.map(dict(zip(el.id, el.now_cost))))
        past = {int(el.loc[el.id == pid, "code"].iloc[0]): s.get("history_past", [])
                for pid, s in summaries.items() if (el.id == pid).any()}
        return cls(rows=rows, elements=meta, teams=bootstrap["teams"],
                   fixtures=fixtures, history_past=past, label=label)

    # ------------------------------------------------------------------ #
    def as_of(self, gw: int) -> tuple[dict, dict, list[dict], pd.DataFrame]:
        """(bootstrap, summaries, fixtures, actuals) as they stood before `gw`."""
        before = self.rows[self.rows["round"] < gw]
        this = self.rows[self.rows["round"] == gw]

        totals = before.groupby("id")[SUM_FIELDS].sum(min_count=1)
        # Price at the deadline: the value recorded for that round, else the
        # last one seen, else the opening price.
        price_now = this.groupby("id").value.first()
        price_prev = before.sort_values("round").groupby("id").value.last()

        elements = []
        for _, m in self.elements.iterrows():
            pid = int(m.id)
            tot = totals.loc[pid] if pid in totals.index else None
            price = price_now.get(pid, price_prev.get(pid, np.nan))
            if pd.isna(price):
                continue  # never priced -> not in the game at this point
            e = {
                "id": pid, "code": int(m.code), "web_name": m.web_name,
                "first_name": m.first_name, "second_name": m.second_name,
                "element_type": int(m.element_type), "team": int(m.team),
                "now_cost": float(price), "status": "a",
                "chance_of_playing_next_round": None, "news": "",
                "form": 0.0, "selected_by_percent": 0.0,
            }
            for f_ in SUM_FIELDS:
                e[f_] = float(tot[f_]) if tot is not None and pd.notnull(tot[f_]) else 0.0
            elements.append(e)

        events = [{"id": r, "name": f"Gameweek {r}", "deadline_time": "",
                   "finished": r < gw, "is_current": r == gw - 1, "is_next": r == gw,
                   "is_previous": r == gw - 2} for r in range(1, 39)]
        bootstrap = {"elements": elements, "teams": self.teams, "events": events,
                     "element_types": [{"id": k, "singular_name_short": v}
                                       for v, k in POS_CODE.items()]}

        summaries: dict[int, dict] = {}
        hist_cols = ["id", "round", "kickoff_time", "value", "was_home",
                     "opponent_team"] + SUM_FIELDS
        hist_rows = (before[[c for c in hist_cols if c in before.columns]]
                     .to_dict("records"))
        by_id: dict[int, list] = {}
        for h in hist_rows:
            by_id.setdefault(int(h["id"]), []).append(h)
        code_of = dict(zip(self.elements.id, self.elements.code))
        for e in elements:
            pid = e["id"]
            summaries[pid] = {"history": by_id.get(pid, []),
                              "history_past": self.history_past.get(code_of.get(pid), [])}

        fixtures = []
        for f in self.fixtures:
            f2 = dict(f)
            ev = f2.get("event")
            f2["finished"] = bool(ev is not None and ev < gw)
            fixtures.append(f2)

        actuals = (this.groupby("id").agg(actual=("total_points", "sum"),
                                          actual_minutes=("minutes", "sum"),
                                          xp=("xP", "sum"),
                                          games=("round", "count"))
                   .reset_index())
        return bootstrap, summaries, fixtures, actuals


# ---------------------------------------------------------------------- #
# scoring
# ---------------------------------------------------------------------- #

def _xi_points(df: pd.DataFrame, score_col: str, actual_col: str = "actual",
               captain: bool = True) -> float:
    """Actual points of the best legal XI chosen by `score_col`."""
    if df.empty or df[score_col].isna().all():
        return float("nan")
    pos = dict(zip(df.id, df.pos))
    ep = dict(zip(df.id, df[score_col].fillna(0.0)))
    try:
        line = best_xi_for_gw(df.id.tolist(), pos, ep)
    except ValueError:
        return float("nan")
    act = dict(zip(df.id, df[actual_col].fillna(0.0)))
    pts = sum(act[p] for p in line.xi)
    if captain and line.captain is not None:
        pts += act[line.captain]
    return float(pts)


SHORTLIST = {"GK": 2, "DEF": 6, "MID": 6, "FWD": 4}


def _shortlist_points(df: pd.DataFrame, score_col: str) -> float:
    """Mean actual points of the top few per position by `score_col`.

    Eighteen players rather than eleven, and no formation choice, so it is a
    steadier read on ranking quality than the XI - one lucky hat-trick moves
    it far less.
    """
    tot, n = 0.0, 0
    for pos, k in SHORTLIST.items():
        top = df[df.pos == pos].nlargest(k, score_col)
        tot += float(top.actual.sum()); n += len(top)
    return tot / n if n else float("nan")


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 5:
        return float("nan")
    return float(a.rank().corr(b.rank()))


def evaluate_gameweek(log: GameweekLog, gw: int,
                      params: ModelParams = DEFAULT_PARAMS) -> tuple[dict, pd.DataFrame]:
    """Project GW `gw` using only earlier data; score against actuals."""
    bootstrap, summaries, fixtures, actuals = log.as_of(gw)
    teams = build_teams(bootstrap)
    fx = build_fixtures(fixtures, teams, gw, 1, params=params)
    if fx.empty:
        return {"gw": gw, "n": 0}, pd.DataFrame()
    players = build_players(bootstrap, summaries, teams, gw - 1, params=params)
    season = Season(players=players, teams=teams.reset_index(), fixtures=fx,
                    events=pd.DataFrame(bootstrap["events"]), next_gw=gw,
                    current_gw=gw - 1, gws_played=gw - 1)
    _, summary = project(season, horizon=1, params=params)

    df = summary.merge(actuals, on="id", how="left")
    df["actual"] = df.actual.fillna(0.0)
    df["actual_minutes"] = df.actual_minutes.fillna(0.0)
    df["games"] = df.games.fillna(0).astype(int)

    # A naive benchmark: season-to-date points per appearance, no fixtures,
    # no minutes model. If we cannot beat this, the model is not earning
    # its keep.
    games_so_far = max(gw - 1, 1)
    df["naive"] = np.where(df.curr_minutes >= 90,
                           df.curr_points / games_so_far, 0.0)

    # Score on players who were realistically selectable: projected to
    # feature. Scoring the ~400 who were never going to play rewards nothing.
    sel = df[df.xmins >= 30].copy()

    metrics = {
        "gw": gw, "n": int(len(sel)),
        "mae": float((sel.ep_next - sel.actual).abs().mean()),
        "bias": float((sel.ep_next - sel.actual).mean()),
        "spearman": _spearman(sel.ep_next, sel.actual),
        "spearman_naive": _spearman(sel.naive, sel.actual),
        "xi_model": _xi_points(df, "ep_next"),
        "xi_naive": _xi_points(df, "naive"),
        "xi_oracle": _xi_points(df, "actual"),
        "shortlist_model": _shortlist_points(df, "ep_next"),
        "shortlist_naive": _shortlist_points(df, "naive"),
        "captain_model": float(df.loc[df.ep_next.idxmax(), "actual"]) if len(df) else np.nan,
        "captain_best": float(df.actual.max()) if len(df) else np.nan,
        "captain_naive": float(df.loc[df.naive.idxmax(), "actual"]) if len(df) else np.nan,
    }
    if sel.xp.notna().any() and sel.xp.abs().sum() > 0:
        metrics["spearman_fpl"] = _spearman(sel.xp, sel.actual)
        metrics["xi_fpl"] = _xi_points(df, "xp")
        metrics["captain_fpl"] = float(df.loc[df.xp.idxmax(), "actual"])
    return metrics, df


def run_backtest(log: GameweekLog, gws: list[int] | None = None,
                 params: ModelParams = DEFAULT_PARAMS,
                 verbose: bool = False) -> pd.DataFrame:
    """Evaluate every gameweek in `gws` (default: every completed one after GW1)."""
    if gws is None:
        gws = [g for g in log.rounds if g >= 2]
    out = []
    t0 = time.time()
    for gw in gws:
        m, _ = evaluate_gameweek(log, gw, params)
        out.append(m)
        if verbose:
            print(f"  GW{gw:>2}  xi {m.get('xi_model', float('nan')):5.1f}  "
                  f"naive {m.get('xi_naive', float('nan')):5.1f}  "
                  f"rho {m.get('spearman', float('nan')):.3f}  "
                  f"({time.time() - t0:.0f}s)")
    return pd.DataFrame(out)


def summarise(results: pd.DataFrame) -> dict:
    """Headline numbers across gameweeks."""
    r = results[results.n > 0]
    if r.empty:
        return {}
    s = {
        "gameweeks": int(len(r)),
        "xi_model_mean": float(r.xi_model.mean()),
        "xi_naive_mean": float(r.xi_naive.mean()),
        "xi_oracle_mean": float(r.xi_oracle.mean()),
        "mae": float(r.mae.mean()),
        "bias": float(r.bias.mean()),
        "spearman": float(r.spearman.mean()),
        "spearman_naive": float(r.spearman_naive.mean()),
        "shortlist_model": float(r.shortlist_model.mean()),
        "shortlist_naive": float(r.shortlist_naive.mean()),
        "captain_model_mean": float(r.captain_model.mean()),
        "captain_naive_mean": float(r.captain_naive.mean()),
        "captain_best_mean": float(r.captain_best.mean()),
        "beats_naive_weeks": int((r.xi_model > r.xi_naive).sum()),
    }
    if "xi_fpl" in r.columns and r.xi_fpl.notna().any():
        s["xi_fpl_mean"] = float(r.xi_fpl.mean())
        s["spearman_fpl"] = float(r.spearman_fpl.mean())
        s["captain_fpl_mean"] = float(r.captain_fpl.mean())
        s["beats_fpl_weeks"] = int((r.xi_model > r.xi_fpl).sum())
    return s


# ---------------------------------------------------------------------- #
# tuning
# ---------------------------------------------------------------------- #

def tune(log: GameweekLog, grid: dict[str, list], gws: list[int] | None = None,
         base: ModelParams = DEFAULT_PARAMS, objective: str = "xi_model_mean",
         verbose: bool = True) -> tuple[ModelParams, pd.DataFrame]:
    """Coordinate search over `grid`, one parameter at a time, best-first.

    A full grid would be thousands of model rebuilds. Coordinate descent
    finds the same optimum in a fraction of the time when the parameters are
    roughly independent, which these are.
    """
    current = base
    rows = []
    best_score = summarise(run_backtest(log, gws, current))[objective]
    if verbose:
        print(f"baseline {objective} = {best_score:.3f}")
    for name, values in grid.items():
        for v in values:
            cand = current.replace(**{name: v})
            res = summarise(run_backtest(log, gws, cand))
            score = res[objective]
            rows.append({"param": name, "value": v, objective: score,
                         "spearman": res["spearman"], "mae": res["mae"],
                         "shortlist": res["shortlist_model"]})
            if verbose:
                flag = "  <- best" if score > best_score + 1e-9 else ""
                print(f"  {name}={v!s:<10} {objective}={score:.3f}  "
                      f"short={res['shortlist_model']:.3f}  rho={res['spearman']:.3f}  "
                      f"mae={res['mae']:.3f}{flag}")
            if score > best_score + 1e-9:
                best_score, current = score, cand
    return current, pd.DataFrame(rows)
