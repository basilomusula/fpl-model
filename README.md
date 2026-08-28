# FPL squad model

A transparent expected-points model for Fantasy Premier League. It pulls live
data from the official FPL API, projects every player's points for the coming
gameweeks, and then tells you four things:

1. the best 15-man squad you could buy for £100m,
2. the best starting XI, bench order, captain and vice out of any 15,
3. which transfers are actually worth making (including whether a −4 hit pays),
4. a ranked table of every player, exportable to CSV.

It writes a self-contained HTML dashboard alongside the CSVs.

---

## Quick start

```bash
pip install -r requirements.txt

# build the best £100m squad from scratch
python run_fpl.py

# work with the squad you already own
python run_fpl.py --squad my_squad.json

# or let it pull your squad straight from FPL (once the season has started)
python run_fpl.py --team-id 1234567
```

Everything lands in `output/`:

| File | What it is |
|---|---|
| `fpl_dashboard.html` | the dashboard — open it in any browser |
| `player_rankings.csv` | every player, ranked, with the model's working |
| `squad.csv` | the selected 15 with XI and bench order |
| `transfers.csv` | ranked transfer options (only when you supply a squad) |

The first run downloads a history file for each of ~700 players, which takes a
minute or two. It is cached in `.fpl_cache/`, so later runs take seconds.

---

## Telling it which players you own

Create a JSON file (see `my_squad.example.json`):

```json
{
  "bank": 0.5,
  "free_transfers": 1,
  "players": ["Raya", "Dubravka", "Gabriel", "Virgil", "...", "Haaland"]
}
```

Names are matched against the short names FPL uses on the site. Where two
players share one — there are usually a few Andersons — the model stops and
lists the options so you can write `"Anderson (NFO)"` instead. Numeric FPL
element ids work too.

---

## How the projection works

Every player gets a **baseline scoring rate** in points per 90 minutes, built
from what they have actually done:

* previous Premier League seasons, most recent weighted fully and the one
  before it half, and
* this season's totals, which take over gradually — by about ten full matches
  of minutes they dominate — with recent form nudging the number once enough
  gameweeks have been played.

Thin evidence is shrunk toward two priors: a replacement-level baseline, and a
**price-implied prior** fitted on the players who *do* have a track record. That
last part matters in August, when new signings and promoted clubs have no
Premier League history at all — the model falls back on the fact that FPL
itself priced a player at £8.5m for a reason.

That baseline is then split into three buckets according to where the player's
points have historically come from — **attacking returns**, **clean sheets**,
and **everything else** (appearance points, defensive contributions, cards,
saves). Only the first two respond to fixtures:

* Each upcoming fixture is turned into a pair of expected-goals numbers from
  FPL's published team attack and defence strengths, separately for home and
  away, blended 70/30 with FPL's own 1–5 difficulty rating.
* FPL does not publish those attack and defence ratings until a season is
  under way. When they are missing the model says so on startup and falls
  back — first to the overall team rating, then to fixture difficulty alone —
  rather than producing numbers from nothing.
* Attacking points scale with how many goals the player's team is expected to
  score. Clean-sheet points scale with the shutout probability, `exp(−xGA)`.
* Goalkeepers and defenders additionally carry the goals-conceded deduction,
  computed exactly over a Poisson goals distribution rather than approximated.

Finally everything is multiplied by **expected minutes** — a start probability
and typical minutes-per-start, both learned from history — and by an
availability factor read off the injury flags and the published chance of
playing.

The advantage of splitting it this way is that bonus points, defensive
contribution points and every other quirk of the scoring system are already
baked into the baseline. The model never has to guess a coefficient for them.

Points over the horizon are discounted 12% per gameweek, so a good fixture next
week counts for more than a good fixture in five weeks' time. Double gameweeks
are summed automatically and blanks simply contribute nothing.

## How the selection works

Choosing 15 players under a budget, two per-position quotas and a three-per-club
cap is a knapsack problem, so it is solved exactly with integer programming
(PuLP/CBC). The objective is deliberately *not* "the 15 highest scorers" —
that buys an expensive bench that never plays. Starters are worth their full
projection, bench players 15% of it, and the captain is counted twice, so the
solver naturally arrives at the classic FPL shape: money concentrated in the
XI, cheap enablers on the bench.

Choosing the XI out of a known 15 is tiny by comparison — there are only eight
legal formations — so it is brute-forced. That keeps the transfer search fast:
every candidate swap is scored by re-picking the best XI for each gameweek in
the horizon, which means upgrading a player who never starts correctly scores
close to zero.

---

## Options

```
--squad FILE            JSON file with your 15 players
--team-id N             pull your squad from FPL instead (needs a played gameweek)
--budget 100.0          squad budget when building from scratch
--bank 0.0              money in the bank
--free-transfers 1      how many transfers are free this week
--horizon 5             gameweeks to plan over
--gw N                  override which gameweek is being planned
--lock "Salah" ...      players that must be in the squad
--ban "Haaland" ...     players to exclude entirely
--min-availability 0.75 drop players less likely than this to be fit
--out output            output directory
--refresh               ignore cached prices and injury news
--offline               run entirely from cache
--no-history            skip per-player history (fast, much weaker in August)
--top-only N            only fetch history for the N most expensive players
--no-dashboard          CSVs only
--title "..."           heading shown on the dashboard
```

Useful combinations:

```bash
# plan a wildcard over a longer window
python run_fpl.py --horizon 8 --budget 100.0

# build a squad around players you refuse to sell
python run_fpl.py --squad my_squad.json --lock "M.Salah" "Haaland"

# a fast look with fresh prices, no history download
python run_fpl.py --no-history --refresh
```

---

## Putting it on your phone

`python run_fpl.py --site _site` writes an installable web-app copy of the
dashboard — the same page plus a manifest, a service worker and icons — into
`_site/`. Serve that folder from any static host and Chrome on Android will
offer to install it to the home screen, where it opens fullscreen and works
offline.

`.github/workflows/dashboard.yml` does the whole thing for you on a schedule
via GitHub Actions and GitHub Pages: no server, no cost. **`GITHUB_SETUP.md`**
walks through it, including the public-versus-private trade-off.

---

## Testing it without the live API

`tests/make_test_cache.py` builds an offline cache from published CSV
snapshots, so the whole pipeline can be run and checked deterministically:

```bash
python tests/make_test_cache.py <csv_dir> /tmp/testcache
python run_fpl.py --offline --cache /tmp/testcache --out /tmp/out
```

CSV files of the right shape are published at
`github.com/vaastav/Fantasy-Premier-League` (`players_raw.csv`, `teams.csv`,
`fixtures.csv` per season).

---

## Things worth knowing before you trust it

* **A single gameweek is mostly noise.** Expected points are a mean, and the
  variance around them is enormous. The model is useful over 38 gameweeks, not
  over one.
* **It has not read the news.** It knows FPL's injury flags and nothing else —
  no press conferences, no rotation hints, no transfer rumours. Run it with
  `--refresh` close to the deadline and override it when you know better.
* **Set-piece and penalty duties are only in there implicitly**, through past
  returns. A player who has just been handed penalties will be underrated until
  they start converting them.
* **Promoted clubs and new signings lean on the price prior**, which is a much
  blunter instrument than actual data. Treat August projections for those
  players as a starting point for your own judgement.
* **Team strength ratings are FPL's own** and they update slowly, particularly
  in the first few weeks of a season. Before the opening weekend they are
  often not published at all, in which case the model leans on fixture
  difficulty and tells you it is doing so.
* **The rankings table is not club-capped, and shouldn't be.** It answers "who
  are the best players", not "who can I legally own". The three-per-club rule
  applies to the squad the optimiser builds, which is a different thing.
