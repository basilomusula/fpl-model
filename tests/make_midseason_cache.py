"""Build an offline cache that looks like the FPL API part-way through a season.

    python tests/make_midseason_cache.py <csv_dir> <prior_csv_dir> <cache_dir> [gameweek]

Takes the per-gameweek CSVs for a season (merged_gw.csv, players_raw.csv,
teams.csv, fixtures.csv) and writes bootstrap, fixtures and element-summary
files as they would have stood before `gameweek` (default 10). This is what
lets the scorecard, the recent-form signal and the mid-season code paths be
exercised without the live API.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, ".")

from fpl_model.backtest import GameweekLog  # noqa: E402


def main(csv_dir: str, prior_dir: str, cache_dir: str, gw: int = 10) -> None:
    prior = {}
    for season, fname in (("2024/25", "players_2024-25.csv"),
                          ("2023/24", "players_2023-24.csv")):
        path = os.path.join(prior_dir, fname)
        if os.path.exists(path):
            prior[season] = path
    log = GameweekLog.from_csvs(
        os.path.join(csv_dir, "merged_gw.csv"), os.path.join(csv_dir, "players_raw.csv"),
        os.path.join(csv_dir, "teams.csv"), os.path.join(csv_dir, "fixtures.csv"), prior)
    bootstrap, summaries, fixtures, _ = log.as_of(gw)

    # Make it look like the live API, including its habit of serving some
    # numbers as text - that mismatch once crashed the live scorecard.
    text_fields = ("expected_goals", "expected_assists", "expected_goal_involvements",
                   "expected_goals_conceded", "influence", "creativity", "threat",
                   "ict_index")
    for e in bootstrap["elements"]:
        e.setdefault("chance_of_playing_this_round", None)
        e.setdefault("cost_change_start", 0)
        for f in text_fields + ("form", "points_per_game"):
            e[f] = f"{float(e.get(f) or 0):.2f}"
        e["selected_by_percent"] = f"{float(e.get('selected_by_percent') or 0):.1f}"
    for s in summaries.values():
        for h in s["history"] + s["history_past"]:
            for f in text_fields:
                h[f] = f"{float(h.get(f) or 0):.2f}"
    for ev in bootstrap["events"]:
        ev["deadline_time"] = f"2026-{8 + ev['id'] // 5:02d}-{1 + ev['id'] % 27:02d}T17:30:00Z"

    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, "bootstrap.json"), "w") as fh:
        json.dump(bootstrap, fh)
    with open(os.path.join(cache_dir, "fixtures.json"), "w") as fh:
        json.dump(fixtures, fh)
    for pid, s in summaries.items():
        with open(os.path.join(cache_dir, f"element_{pid}.json"), "w") as fh:
            json.dump({"history": s["history"], "history_past": s["history_past"],
                       "fixtures": []}, fh)
    played = sum(1 for e in bootstrap["elements"] if e["minutes"] > 0)
    print(f"wrote a GW{gw} cache to {cache_dir}: {len(bootstrap['elements'])} players, "
          f"{played} with minutes, {sum(len(s['history']) for s in summaries.values())} "
          "history rows")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3],
         int(sys.argv[4]) if len(sys.argv) > 4 else 10)
