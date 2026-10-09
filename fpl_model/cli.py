"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from .api import FPLClient, FPLError
from .dashboard import build_dashboard
from .features import Season, build_fixtures, build_players, build_teams, parse_events
from .optimiser import (FORMATIONS, best_xi_for_gw, fmt_formation, optimise_squad,
                        parse_formation, squad_horizon_points, suggest_transfers)
from .projection import gw_matrix, project

BAR = "─" * 72


# ---------------------------------------------------------------------- #
# loading
# ---------------------------------------------------------------------- #

def load_season(args) -> Season:
    client = FPLClient(cache_dir=args.cache, offline=args.offline,
                       verbose=not args.quiet)
    if args.refresh and os.path.isdir(args.cache):
        for f in os.listdir(args.cache):
            if f.startswith(("bootstrap", "fixtures", "entry")):
                os.remove(os.path.join(args.cache, f))

    say = (lambda *a: None) if args.quiet else print
    say("Fetching FPL data...")
    boot = client.bootstrap()
    fixtures_json = client.fixtures()

    events, next_gw, current_gw, gws_played = parse_events(boot)
    if args.gw:
        next_gw = args.gw

    teams = build_teams(boot)
    fixtures = build_fixtures(fixtures_json, teams, next_gw, args.horizon)

    summaries: dict[int, dict] = {}
    if not args.no_history:
        ids = [int(e["id"]) for e in boot["elements"]]
        if args.top_only:
            el = pd.DataFrame(boot["elements"])
            ids = el.nlargest(args.top_only, "now_cost").id.astype(int).tolist()
        summaries = client.element_summaries(ids)
        say(f"  loaded history for {len(summaries)} players")

    players = build_players(boot, summaries, teams, gws_played)

    deadline, deadline_iso = "—", ""
    ev = events[events.id == next_gw]
    if len(ev) and ev.deadline_time.iloc[0]:
        deadline_iso = str(ev.deadline_time.iloc[0])
        dt = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
        deadline = dt.strftime("%a %d %b, %H:%M UTC")

    source = teams.attrs.get("strength_source", "unknown")
    if source != "attack/defence split":
        say(f"  note: FPL has not published full team strength ratings yet "
            f"({source}) — fixture difficulty is carrying more of the model")

    return Season(players=players, teams=teams.reset_index(), fixtures=fixtures,
                  events=events, next_gw=next_gw, current_gw=current_gw,
                  gws_played=gws_played,
                  meta={"deadline": deadline, "deadline_iso": deadline_iso,
                        "client": client, "strength_source": source,
                        "bootstrap": boot, "fixtures_json": fixtures_json,
                        "summaries": summaries})


def resolve_players(tokens: list, summary: pd.DataFrame) -> list[int]:
    """Accept ids, 'Salah', or 'Salah (LIV)' and return element ids."""
    out, problems = [], []
    lut = summary.assign(key=summary.name.str.lower()).set_index("key")
    for tok in tokens:
        if isinstance(tok, int) or (isinstance(tok, str) and tok.isdigit()):
            pid = int(tok)
            if pid in set(summary.id):
                out.append(pid)
            else:
                problems.append(tok)
            continue
        text = str(tok).strip()
        club = None
        if "(" in text and text.endswith(")"):
            text, club = text[:text.index("(")].strip(), text[text.index("(") + 1:-1].strip()
        matches = summary[summary.name.str.lower() == text.lower()]
        if matches.empty:
            matches = summary[summary.name.str.lower().str.contains(text.lower(), regex=False)]
        if matches.empty:
            matches = summary[summary.full_name.str.lower().str.contains(text.lower(), regex=False)]
        if club:
            matches = matches[matches.team_short.str.lower() == club.lower()]
        if len(matches) == 0:
            problems.append(tok)
        elif len(matches) > 1:
            opts = ", ".join(f"{r['name']} ({r['team_short']})"
                             for _, r in matches.head(6).iterrows())
            problems.append(f"{tok} → ambiguous: {opts}")
        else:
            out.append(int(matches.iloc[0].id))
    if problems:
        raise SystemExit("Could not resolve these squad entries:\n  - "
                         + "\n  - ".join(str(p) for p in problems)
                         + "\n(use 'Name (CLUB)' to disambiguate, or the numeric id)")
    return out


def load_my_squad(args, summary: pd.DataFrame) -> tuple[list[int], float, int] | None:
    if not args.team_id:
        env_id = os.environ.get("FPL_TEAM_ID", "").strip()
        if env_id.isdigit():
            args.team_id = int(env_id)
    if args.team_id:
        client: FPLClient = args._client
        gw = args.picks_gw or getattr(args, "_latest_finished_gw", None) or 1
        try:
            picks = client.entry_picks(args.team_id, gw)
        except FPLError:
            if args.squad:
                print(f"  ! could not load team {args.team_id} picks for GW{gw}; "
                      "falling back to the squad file")
                args.team_id = None
                return load_my_squad(args, summary)
            raise SystemExit(
                f"FPL has no saved squad for team {args.team_id} in GW{gw}. "
                "Before the season starts this endpoint is empty — use --squad "
                "with a JSON file instead.")
        print(f"  loaded your GW{gw} squad from FPL team {args.team_id}")
        ids = [int(p["element"]) for p in picks["picks"]]
        bank = picks.get("entry_history", {}).get("bank", 0) / 10.0
        return ids, bank, args.free_transfers

    if args.squad:
        with open(args.squad, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if isinstance(blob, list):
            blob = {"players": blob}
        ids = resolve_players(blob["players"], summary)
        if len(ids) != 15:
            raise SystemExit(f"squad file has {len(ids)} players, expected 15")
        bank = float(blob.get("bank", args.bank))
        ft = int(blob.get("free_transfers", args.free_transfers))
        return ids, bank, ft
    return None


# ---------------------------------------------------------------------- #
# reporting
# ---------------------------------------------------------------------- #

def print_lineup(line, summary_ix: pd.DataFrame, gw: int, say=print) -> None:
    def row(pid, tag=""):
        p = summary_ix.loc[pid]
        return (f"  {tag:<4}{p['name'][:18]:<19}{p['pos']:<5}{p['team_short']:<5}"
                f"£{p['price']:>4.1f}  {p['ep_next']:>5.2f}  {p['ep_horizon']:>6.2f}  "
                f"{p['fixture_run'][:34]}")

    say(f"\n{BAR}\nSTARTING XI  ({line.formation})"
          f"{'':<12}{'£m':>8}{f'GW{gw}':>8}{'next':>8}")
    order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
    for pid in sorted(line.xi, key=lambda p: (order[summary_ix.loc[p, 'pos']],
                                              -summary_ix.loc[p, 'ep_next'])):
        tag = "(C)" if pid == line.captain else "(V)" if pid == line.vice_captain else ""
        say(row(pid, tag))
    say("\nBENCH")
    if line.bench_gk:
        say(row(line.bench_gk, "GK"))
    for i, pid in enumerate(line.bench, 1):
        say(row(pid, f"{i}."))


def build_ticker(season, gws: list[int]) -> dict:
    """Club-by-gameweek fixture difficulty, easiest overall run first.

    Blank gameweeks appear as empty cells and doubles list both fixtures, so
    the grid stays honest about what a club actually plays.
    """
    fx = season.fixtures[season.fixtures.gw.isin(gws)]
    if fx.empty:
        return {"gws": [], "rows": []}
    shorts = season.teams.set_index("id").short_name.to_dict()
    names = season.teams.set_index("id").name.to_dict()

    rows = []
    for team_id, grp in fx.groupby("team"):
        cells = {gw: [] for gw in gws}
        for _, r in grp.sort_values("gw").iterrows():
            cells[int(r.gw)].append({"opp": str(r.opp_short),
                                     "home": bool(r.is_home),
                                     "fdr": int(r.fdr)})
        played = [c for cs in cells.values() for c in cs]
        rows.append({
            "club": shorts.get(team_id, "?"),
            "name": names.get(team_id, ""),
            "avg": round(sum(c["fdr"] for c in played) / len(played), 2) if played else 0,
            "n": len(played),
            "cells": {str(gw): cells[gw] for gw in gws},
        })
    rows.sort(key=lambda r: (r["avg"], -r["n"]))
    return {"gws": gws, "rows": rows}


def build_scorecard(season: Season, args, say) -> dict | None:
    """Replay the model over completed gameweeks and score it honestly."""
    from .backtest import GameweekLog, run_backtest, summarise
    from .features import DEFAULT_PARAMS

    played = season.gws_played
    summaries = season.meta.get("summaries") or {}
    if played < 2:
        say("  scorecard: fewer than two gameweeks played, nothing to score yet")
        return None
    if not summaries:
        say("  scorecard: needs player history (run without --no-history)")
        return None
    log = GameweekLog.from_api(season.meta["bootstrap"], season.meta["fixtures_json"],
                               summaries, label="this season")
    gws = [g for g in log.rounds if 2 <= g <= played][-args.scorecard_weeks:]
    if not gws:
        return None
    say(f"  scorecard: replaying GW{gws[0]}–GW{gws[-1]}...")
    res = run_backtest(log, gws, DEFAULT_PARAMS)
    res.to_csv(os.path.join(args.out, "scorecard.csv"), index=False)
    s = summarise(res)
    if not s:
        return None
    say(f"  scorecard: model XI {s['xi_model_mean']:.1f} pts/wk vs naive "
        f"{s['xi_naive_mean']:.1f} (ceiling {s['xi_oracle_mean']:.0f}) · "
        f"rank corr {s['spearman']:.2f} · beat naive {s['beats_naive_weeks']}/{s['gameweeks']} wks")
    rows = res[res.n > 0][["gw", "xi_model", "xi_naive", "xi_oracle", "captain_model",
                           "captain_best", "spearman", "mae", "bias"]]
    return {"summary": s, "rows": rows.round(2).to_dict("records")}


def _player_payload(summary: pd.DataFrame, gw_ep: dict, gws: list[int]) -> list[dict]:
    """Every priced player with the fields the in-page squad picker needs."""
    cols = ["id", "name", "pos", "team", "team_short", "price", "ep_next",
            "ep_horizon", "ep_weighted", "value", "xmins", "p_app",
            "availability", "news", "curr_minutes", "p_start", "mean_fdr",
            "fixture_list", "selected_by_percent"]
    df = summary[[c for c in cols if c in summary.columns]].copy()
    df = df[df.price > 0]
    out = []
    for r in df.to_dict("records"):
        r["ep"] = {str(g): round(gw_ep.get(int(r["id"]), {}).get(g, 0.0), 3) for g in gws}
        for k in ("ep_next", "ep_horizon", "ep_weighted", "value", "xmins",
                  "p_app", "availability", "price", "mean_fdr", "p_start"):
            try:
                r[k] = round(float(r.get(k) or 0), 3)
            except (TypeError, ValueError):
                r[k] = 0.0
        r["curr_minutes"] = int(r.get("curr_minutes") or 0)
        r["team"] = int(r["team"])
        r["news"] = str(r.get("news") or "")
        try:
            r["selected_by_percent"] = float(r.get("selected_by_percent") or 0)
        except (TypeError, ValueError):
            r["selected_by_percent"] = 0.0
        out.append(r)
    return out


def to_records(summary_ix: pd.DataFrame, ids: list[int]) -> list[dict]:
    cols = ["id", "name", "pos", "team_short", "price", "ep_next", "ep_horizon",
            "fixture_run", "fixture_list", "news", "availability", "xmins",
            "curr_minutes", "p_start", "value", "selected_by_percent"]
    df = summary_ix.loc[ids].reset_index()
    return df[[c for c in cols if c in df.columns]].to_dict("records")


# ---------------------------------------------------------------------- #
# main
# ---------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fpl", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Fantasy Premier League squad model.",
        epilog="""examples:
  python run_fpl.py                          build the best £100m squad from scratch
  python run_fpl.py --budget 101.5           allow for a squad you have already sold into
  python run_fpl.py --squad my_squad.json    pick the XI and suggest transfers for your squad
  python run_fpl.py --team-id 1234567        same, pulling your squad from FPL
  python run_fpl.py --horizon 8              plan further ahead
""")
    p.add_argument("--squad", help="JSON file with your 15 players")
    p.add_argument("--team-id", type=int, help="your FPL team id (from the site URL)")
    p.add_argument("--picks-gw", type=int, help="gameweek to read your saved picks from")
    p.add_argument("--budget", type=float, default=100.0, help="squad budget (default 100.0)")
    p.add_argument("--bank", type=float, default=0.0, help="money in the bank")
    p.add_argument("--free-transfers", type=int, default=1)
    p.add_argument("--horizon", type=int, default=5, help="gameweeks to plan over (default 5)")
    p.add_argument("--gw", type=int, help="override the gameweek being planned")
    p.add_argument("--lock", nargs="*", default=[], help="players that must be in the squad")
    p.add_argument("--ban", nargs="*", default=[], help="players to exclude entirely")
    p.add_argument("--min-availability", type=float, default=0.75,
                   help="drop players less likely than this to be fit (default 0.75)")
    p.add_argument("--formation", default="auto", metavar="D-M-F",
                   help="fix the starting formation, e.g. 3-4-3 (default: auto, "
                        "whichever shape projects best each week)")
    p.add_argument("--min-minutes", type=float, default=0.0, metavar="MINS",
                   help="ignore players expected to play fewer than this many "
                        "minutes per game — 60 filters out most rotation risks")
    p.add_argument("--minutes-weight", type=float, default=None, metavar="K",
                   help="how hard to penalise part-players (default 1.25; "
                        "1.0 is neutral, higher favours nailed-on starters)")
    p.add_argument("--out", default="output", help="output directory")
    p.add_argument("--cache", default=".fpl_cache")
    p.add_argument("--refresh", action="store_true", help="ignore cached prices and news")
    p.add_argument("--offline", action="store_true", help="use only what is cached")
    p.add_argument("--no-history", action="store_true",
                   help="skip per-player history (much faster, weaker early-season)")
    p.add_argument("--top-only", type=int,
                   help="only fetch history for the N most expensive players")
    p.add_argument("--site", metavar="DIR",
                   help="also write an installable web-app copy into DIR "
                        "(for GitHub Pages or any static host)")
    p.add_argument("--scorecard", action="store_true",
                   help="replay the model over this season's completed gameweeks "
                        "and report how accurate it was (needs player history)")
    p.add_argument("--scorecard-weeks", type=int, default=12,
                   help="how many recent gameweeks the scorecard covers")
    p.add_argument("--no-dashboard", action="store_true")
    p.add_argument("--title", default="Fantasy Premier League — squad model",
                   help="heading shown on the dashboard")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv=None) -> int:
    # Windows consoles still default to a legacy code page in some setups,
    # which would blow up on the box-drawing characters and the pound sign.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    say = (lambda *a, **k: None) if args.quiet else print

    season = load_season(args)
    args._client = season.meta["client"]
    args._latest_finished_gw = season.gws_played or None

    from .projection import MINUTES_EXPONENT
    per_gw, summary = project(
        season, horizon=args.horizon,
        minutes_exponent=(args.minutes_weight if args.minutes_weight is not None
                          else MINUTES_EXPONENT))
    summary_ix = summary.set_index("id")
    gws = sorted(per_gw.gw.unique())[:args.horizon]

    os.makedirs(args.out, exist_ok=True)
    say(f"\n{BAR}\nGameweek {season.next_gw} · deadline {season.meta['deadline']}"
        f" · {season.gws_played} gameweeks played"
        f"\nPlanning horizon: GW{gws[0]}–GW{gws[-1]}\n{BAR}")

    rank_path = os.path.join(args.out, "player_rankings.csv")
    rank_cols = ["rank_overall", "rank_pos", "name", "full_name", "pos",
                 "team_short", "price", "ep_next", "ep_horizon", "ep_weighted",
                 "value", "xmins", "curr_minutes", "p_start", "availability",
                 "base_p90", "fixtures_n", "mean_fdr", "fixture_run", "news",
                 "selected_by_percent"]
    summary[[c for c in rank_cols if c in summary.columns]].to_csv(rank_path, index=False)
    say(f"\nRankings written to {rank_path}")

    say("\nTOP 15 PROJECTED PLAYERS (next "
        f"{len(gws)} GWs)\n{'':<3}{'player':<19}{'pos':<5}{'club':<6}{'£m':>5}"
        f"{'  GW':>7}{'  next':>7}  fixtures")
    for i, (_, r) in enumerate(summary.head(15).iterrows(), 1):
        say(f"{i:<3}{r['name'][:18]:<19}{r['pos']:<5}{r['team_short']:<6}"
            f"{r['price']:>5.1f}{r['ep_next']:>7.2f}{r['ep_horizon']:>7.2f}  "
            f"{r['fixture_run'][:34]}")

    mine = load_my_squad(args, summary)
    transfers: list[dict] = []
    try:
        shape = parse_formation(args.formation)
    except ValueError as exc:
        raise SystemExit(str(exc))

    gw_ep = gw_matrix(per_gw, summary.id.tolist(), gws)
    pos_map = summary_ix.pos.to_dict()

    # The model's own best 15 is built for every formation - the page lets you
    # switch between them, and each is the benchmark for the others. One
    # solve takes a fraction of a second, so all nine cost almost nothing.
    locked = resolve_players(args.lock, summary) if args.lock else []
    banned = resolve_players(args.ban, summary) if args.ban else []
    model_squads: dict[str, dict] = {}
    for shp in [None] + FORMATIONS:
        try:
            b = optimise_squad(summary, budget=args.budget,
                               min_availability=args.min_availability,
                               min_minutes=args.min_minutes,
                               locked=locked, banned=banned,
                               formation=shp, verbose=False)
        except (RuntimeError, ValueError):
            continue    # e.g. locked players that cannot fit this shape
        ep1 = {pid: gw_ep.get(pid, {}).get(gws[0], 0.0) for pid in b["squad"]}
        first = best_xi_for_gw(b["squad"], pos_map, ep1, formation=shp)
        model_squads[fmt_formation(shp)] = {
            "squad": [int(x) for x in b["squad"]],
            "cost": b["cost"],
            "bank": round(args.budget - b["cost"], 1),
            "xi_next": round(first.xi_points + ep1.get(first.captain, 0.0), 2),
            "horizon": round(squad_horizon_points(b["squad"], pos_map, gw_ep, gws,
                                                  formation=shp), 2),
            "first_formation": first.formation,
        }
    key = fmt_formation(shape)
    if key not in model_squads:
        raise SystemExit(f"could not build a legal squad in {key} with these settings")
    model_build = model_squads[key]

    if not args.quiet:
        best_h = model_squads["auto"]["horizon"]
        say(f"\n{BAR}\nBEST £{args.budget:.1f}m SQUAD BY FORMATION"
            f"   (projected points, next {len(gws)} GWs, captain included)")
        for k in ["auto"] + [fmt_formation(f) for f in FORMATIONS]:
            if k not in model_squads:
                continue
            ms = model_squads[k]
            gap = ms["horizon"] - best_h
            note = (f"  (fields {ms['first_formation']} this week)" if k == "auto"
                    else f"  {gap:+.1f}" if abs(gap) >= 0.05 else "  = best")
            mark = " ←" if k == key else ""
            say(f"  {k:<6} {ms['horizon']:>7.1f}{note}{mark}")

    if mine:
        squad_ids, bank, free_transfers = mine
        say(f"\n{BAR}\nYOUR SQUAD  (bank £{bank:.1f}m, "
            f"{free_transfers} free transfer{'s' if free_transfers != 1 else ''})")
        budget_used = float(summary_ix.loc[squad_ids].price.sum())
    else:
        say(f"\n{BAR}\nBuilding the best £{args.budget:.1f}m squad...")
        squad_ids = model_build["squad"]
        budget_used = model_build["cost"]
        bank = round(args.budget - budget_used, 1)
        free_transfers = args.free_transfers

    first_ep = {pid: gw_ep.get(pid, {}).get(gws[0], 0.0) for pid in squad_ids}
    reliability = {pid: float(summary_ix.loc[pid, "p_app"]) * float(summary_ix.loc[pid, "availability"])
                   for pid in squad_ids}
    line = best_xi_for_gw(squad_ids, pos_map, first_ep, reliability=reliability,
                          formation=shape)
    horizon_pts = squad_horizon_points(squad_ids, pos_map, gw_ep, gws,
                                       formation=shape)

    print_lineup(line, summary_ix, season.next_gw, say)
    say(f"\n  Squad cost £{budget_used:.1f}m · bank £{bank:.1f}m")
    say(f"  Projected GW{season.next_gw} XI: {line.xi_points:.1f} pts "
        f"(+{first_ep.get(line.captain, 0):.1f} captain) · "
        f"next {len(gws)} GWs: {horizon_pts:.0f} pts")
    if line.vice_captain is not None:
        say(f"  Captain {summary_ix.loc[line.captain, 'name']} · "
            f"vice {summary_ix.loc[line.vice_captain, 'name']} "
            f"({first_ep.get(line.vice_captain, 0):.1f} pts, "
            f"{100 * reliability.get(line.vice_captain, 1):.0f}% to feature)")

    squad_rows = to_records(summary_ix, line.xi
                            + ([line.bench_gk] if line.bench_gk else [])
                            + line.bench)
    (pd.DataFrame(squad_rows).drop(columns=["fixture_list"], errors="ignore")
     .to_csv(os.path.join(args.out, "squad.csv"), index=False))

    if mine:
        say(f"\n{BAR}\nTransfer options...")
        transfers, base_pts, sq_value = suggest_transfers(
            squad_ids, summary, gw_ep, gws, bank=bank,
            free_transfers=free_transfers,
            min_availability=args.min_availability,
            min_minutes=args.min_minutes, formation=shape)
        if not transfers:
            say("  Nothing improves this squad over the horizon — save the transfer.")
        for t in transfers[:8]:
            arrow = "{} → {}".format(", ".join(t["out_names"]),
                                     ", ".join(t["in_names"]))
            hit = " − {}".format(t["hit"]) if t["hit"] else "    "
            tick = "  ✓" if t["worth_it"] else ""
            say(f"  {arrow:<44}bank {-t['cost']:>+5.1f}m  "
                f"gain {t['gain']:>5.2f}{hit}  net {t['net_gain']:>+5.2f}{tick}")
        if transfers:
            pd.DataFrame(transfers).to_csv(
                os.path.join(args.out, "transfers.csv"), index=False)

    scorecard = None
    if args.scorecard:
        scorecard = build_scorecard(season, args, say)

    if not args.no_dashboard:
        ctx = {
            "scorecard": scorecard,
            "title": args.title,
            "next_gw": season.next_gw, "deadline": season.meta["deadline"],
            "deadline_iso": season.meta.get("deadline_iso", ""),
            "strength_source": season.meta.get("strength_source", ""),
            "gws_played": season.gws_played,
            "horizon": len(gws), "formation": line.formation,
            "xi": to_records(summary_ix, line.xi),
            "bench": to_records(summary_ix, line.bench),
            "bench_gk": (to_records(summary_ix, [line.bench_gk])[0]
                         if line.bench_gk else None),
            "captain": line.captain, "vice_captain": line.vice_captain,
            "captain_ep": first_ep.get(line.captain, 0.0),
            "vice_ep": first_ep.get(line.vice_captain, 0.0),
            "vice_reliability": reliability.get(line.vice_captain, 1.0),
            "xi_points": line.xi_points,
            "squad_cost": budget_used, "bank": bank,
            "squad_horizon": horizon_pts,
            "transfers": transfers,
            "ticker": build_ticker(season, gws),
            "gws": [int(g) for g in gws],
            "initial_squad": squad_ids if mine else [],
            "model_squad": model_squads["auto"]["squad"],
            "model_bank": model_squads["auto"]["bank"],
            "model_squads": model_squads,
            "formation": key,
            "budget": args.budget,
            "bank_initial": bank, "free_transfers_initial": free_transfers,
            "players": _player_payload(summary, gw_ep, gws),
            "rankings": summary.head(300)[
                ["id", "name", "pos", "team_short", "price", "ep_next",
                 "ep_horizon", "value", "xmins", "curr_minutes", "p_start",
                 "mean_fdr", "fixture_run", "fixture_list", "news",
                 "selected_by_percent"]
            ].fillna("").to_dict("records"),
        }
        page = build_dashboard(ctx)
        html_path = os.path.join(args.out, "fpl_dashboard.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(page)
        say(f"\nDashboard written to {html_path}")

        if args.site:
            from .webapp import build_site
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
            files = build_site(page, args.site, stamp)
            say(f"Installable web app written to {args.site}/ "
                f"({len(files)} files, build {stamp})")

    say("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
