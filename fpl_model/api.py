"""Fantasy Premier League API client with on-disk caching.

All endpoints used here are public and unauthenticated. Nothing is written
anywhere except the cache directory you pass in (default: ./.fpl_cache).
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

BASE = "https://fantasy.premierleague.com/api"

# Cache lifetimes in seconds. Player histories barely move within a gameweek,
# so they get a long TTL; the bootstrap holds prices and injury news and gets
# a short one.
TTL_BOOTSTRAP = 30 * 60
TTL_FIXTURES = 60 * 60
TTL_SUMMARY = 12 * 60 * 60
TTL_ENTRY = 10 * 60

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class FPLError(RuntimeError):
    pass


class FPLClient:
    def __init__(self, cache_dir: str = ".fpl_cache", offline: bool = False,
                 timeout: int = 25, workers: int = 8, verbose: bool = True):
        self.cache_dir = cache_dir
        self.offline = offline
        self.timeout = timeout
        self.workers = workers
        self.verbose = verbose
        os.makedirs(cache_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT,
                                     "Accept": "application/json"})

    # ------------------------------------------------------------------ #
    # low level
    # ------------------------------------------------------------------ #
    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key.replace("/", "_") + ".json")

    def _read_cache(self, key: str, ttl: int) -> Any | None:
        path = self._cache_path(key)
        if not os.path.exists(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if not self.offline and age > ttl:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, key: str, payload: Any) -> None:
        try:
            with open(self._cache_path(key), "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError:
            pass  # a broken cache should never break a run

    def get(self, path: str, key: str, ttl: int, retries: int = 3) -> Any:
        cached = self._read_cache(key, ttl)
        if cached is not None:
            return cached
        if self.offline:
            raise FPLError(f"offline mode and no cached copy of {key}")

        last: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.get(f"{BASE}/{path}", timeout=self.timeout)
                if resp.status_code == 404:
                    raise FPLError(f"404 from FPL for {path}")
                resp.raise_for_status()
                payload = resp.json()
                self._write_cache(key, payload)
                return payload
            except FPLError:
                raise
            except Exception as exc:  # network hiccup / rate limit
                last = exc
                time.sleep(1.5 * (attempt + 1))
        # fall back to a stale cache rather than dying
        stale = self._read_cache(key, ttl=10**9)
        if stale is not None:
            if self.verbose:
                print(f"  ! {path} unreachable, using stale cache")
            return stale
        raise FPLError(f"could not fetch {path}: {last}")

    # ------------------------------------------------------------------ #
    # endpoints
    # ------------------------------------------------------------------ #
    def bootstrap(self) -> dict:
        """Players, teams, positions and the gameweek calendar."""
        return self.get("bootstrap-static/", "bootstrap", TTL_BOOTSTRAP)

    def fixtures(self) -> list[dict]:
        """Every fixture in the season, with FPL's own difficulty ratings."""
        return self.get("fixtures/", "fixtures", TTL_FIXTURES)

    def element_summary(self, player_id: int) -> dict:
        """One player's gameweek log plus their previous-season totals."""
        return self.get(f"element-summary/{player_id}/",
                        f"element_{player_id}", TTL_SUMMARY)

    def element_summaries(self, player_ids: list[int]) -> dict[int, dict]:
        """Fetch many player histories in parallel, tolerating failures."""
        out: dict[int, dict] = {}
        todo = []
        for pid in player_ids:
            cached = self._read_cache(f"element_{pid}", TTL_SUMMARY)
            if cached is not None:
                out[pid] = cached
            else:
                todo.append(pid)

        if todo and self.offline:
            return out
        if todo and self.verbose:
            print(f"  fetching {len(todo)} player histories "
                  f"({len(out)} already cached)...")

        def one(pid: int):
            try:
                return pid, self.element_summary(pid)
            except Exception:
                return pid, None

        if todo:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for i, (pid, data) in enumerate(pool.map(one, todo), 1):
                    if data is not None:
                        out[pid] = data
                    if self.verbose and i % 100 == 0:
                        print(f"    {i}/{len(todo)}")
        return out

    def entry_picks(self, entry_id: int, event: int) -> dict:
        """A manager's saved squad for a finished/current gameweek."""
        return self.get(f"entry/{entry_id}/event/{event}/picks/",
                        f"entry_{entry_id}_{event}", TTL_ENTRY)

    def entry(self, entry_id: int) -> dict:
        return self.get(f"entry/{entry_id}/", f"entry_{entry_id}", TTL_ENTRY)
