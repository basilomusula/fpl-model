#!/usr/bin/env python3
"""Re-tune the model against a full past season.

    python tools/tune.py 2025-26            # downloads the CSVs, runs the search
    python tools/tune.py 2025-26 --quick    # a smaller grid

Downloads the per-gameweek CSVs for that season (and the two before it, for
prior-season history) from github.com/vaastav/Fantasy-Premier-League, runs
the walk-forward backtest with the current defaults, then a coordinate search
over the main assumptions, and prints the settings to paste into ModelParams.

Run this each summer once the previous season's data is complete.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fpl_model.backtest import GameweekLog, run_backtest, summarise, tune  # noqa: E402
from fpl_model.features import DEFAULT_PARAMS  # noqa: E402

RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def fetch(season: str, name: str, dest: str) -> str:
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, f"{season}_{name.replace('/', '_')}")
    if not os.path.exists(path):
        print(f"  downloading {season}/{name}")
        urllib.request.urlretrieve(f"{RAW}/{season}/{name}", path)
    return path


def prev_season(s: str) -> str:
    a, b = s.split("-")
    return f"{int(a) - 1}-{int(b) - 1:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("season", help="e.g. 2025-26")
    ap.add_argument("--data", default=".tune_data")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    s = args.season
    files = {n: fetch(s, n, args.data) for n in
             ("gws/merged_gw.csv", "players_raw.csv", "teams.csv", "fixtures.csv")}
    prior = {}
    for p in (prev_season(s), prev_season(prev_season(s))):
        try:
            prior[p.replace("-", "/")] = fetch(p, "players_raw.csv", args.data)
        except Exception as exc:  # noqa: BLE001
            print(f"  (no prior data for {p}: {exc})")

    log = GameweekLog.from_csvs(files["gws/merged_gw.csv"], files["players_raw.csv"],
                                files["teams.csv"], files["fixtures.csv"], prior, label=s)
    print(f"\n{s}: {len(log.rounds)} gameweeks, {len(log.elements)} players")
    base = summarise(run_backtest(log))
    print(f"current defaults: XI {base['xi_model_mean']:.2f}/wk, shortlist "
          f"{base['shortlist_model']:.3f}, beats naive {base['beats_naive_weeks']}/{base['gameweeks']}")

    grid = {
        "recent_weight": [0.15, 0.3, 0.5],
        "shrink_k": [10.0, 20.0, 30.0],
        "form_window": [3, 4],
        "form_weight": [0.25, 0.35, 0.5],
        "minutes_exponent": [1.0, 1.25, 1.5],
        "att_elasticity": [0.9, 1.2, 1.5],
        "cs_elasticity": [1.0, 1.3],
        "xg_weight": [0.0, 0.25, 0.5],
    }
    if args.quick:
        grid = {k: v[:2] for k, v in grid.items()}
    best, _ = tune(log, grid, base=DEFAULT_PARAMS, verbose=True)
    final = summarise(run_backtest(log, params=best))
    print(f"\nbest: XI {final['xi_model_mean']:.2f}/wk, shortlist {final['shortlist_model']:.3f}, "
          f"beats naive {final['beats_naive_weeks']}/{final['gameweeks']}")
    print("\nPaste into ModelParams in fpl_model/features.py:")
    for k, v in best.__dict__.items():
        print(f"    {k}: {type(v).__name__} = {v!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
