"""Build an offline cache from public CSV snapshots, so the model can be run
and checked without touching the live FPL API.

Usage:
    python tests/make_test_cache.py <csv_dir> <cache_dir>

<csv_dir> should contain players_raw.csv, teams.csv, fixtures.csv for the
season under test, plus players_<season>.csv files for prior seasons (used to
fake the element-summary history_past payloads). Files of that shape are
published at github.com/vaastav/Fantasy-Premier-League.
"""

from __future__ import annotations

import json
import os
import sys

import pandas as pd

HISTORY_FIELDS = ["minutes", "total_points", "goals_scored", "assists",
                  "clean_sheets", "goals_conceded", "saves", "bonus", "bps",
                  "starts", "yellow_cards", "red_cards", "penalties_saved",
                  "expected_goals", "expected_assists"]


def main(csv_dir: str, cache_dir: str) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    players = pd.read_csv(os.path.join(csv_dir, "players_raw.csv"))
    teams = pd.read_csv(os.path.join(csv_dir, "teams.csv"))
    fixtures = pd.read_csv(os.path.join(csv_dir, "fixtures.csv"))

    events = []
    for gw in range(1, 39):
        events.append({
            "id": gw, "name": f"Gameweek {gw}",
            "deadline_time": f"2026-{8 + (gw // 5):02d}-{1 + (gw % 27):02d}T17:30:00Z",
            "finished": False, "is_current": False,
            "is_next": gw == 1, "is_previous": False,
        })

    bootstrap = {
        "events": events,
        "teams": teams.where(pd.notnull(teams), None).to_dict("records"),
        "element_types": [
            {"id": 1, "singular_name_short": "GKP"},
            {"id": 2, "singular_name_short": "DEF"},
            {"id": 3, "singular_name_short": "MID"},
            {"id": 4, "singular_name_short": "FWD"},
        ],
        "elements": players.where(pd.notnull(players), None).to_dict("records"),
    }
    _dump(cache_dir, "bootstrap", bootstrap)

    fx = fixtures.where(pd.notnull(fixtures), None)
    fx = fx.drop(columns=[c for c in ("stats",) if c in fx.columns])
    # The published file is an end-of-season archive; rewind it so every
    # fixture is upcoming, which is the situation the model plans for.
    for col, val in (("finished", False), ("finished_provisional", False),
                     ("started", False)):
        if col in fx.columns:
            fx[col] = val
    _dump(cache_dir, "fixtures", fx.to_dict("records"))

    # Prior seasons, matched on `code` (the only id that is stable year to year).
    past: dict[int, list[dict]] = {}
    for fname in sorted(os.listdir(csv_dir)):
        if not fname.startswith("players_2"):
            continue
        season = fname.replace("players_", "").replace(".csv", "")
        prev = pd.read_csv(os.path.join(csv_dir, fname))
        for _, r in prev.iterrows():
            rec = {"season_name": season}
            for f in HISTORY_FIELDS:
                rec[f] = float(r[f]) if f in prev.columns and pd.notnull(r[f]) else 0.0
            past.setdefault(int(r["code"]), []).append(rec)

    written = 0
    for _, r in players.iterrows():
        hist = past.get(int(r["code"]), [])
        _dump(cache_dir, f"element_{int(r['id'])}",
              {"history_past": hist, "history": [], "fixtures": []})
        written += 1

    print(f"wrote bootstrap, fixtures and {written} player histories to {cache_dir}")
    print(f"  {sum(1 for _, r in players.iterrows() if past.get(int(r['code'])))} "
          "players have prior-season data")


def _dump(cache_dir: str, key: str, payload) -> None:
    with open(os.path.join(cache_dir, f"{key}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
