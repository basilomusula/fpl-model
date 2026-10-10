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

Three ways, any of which works:

**In the dashboard.** Open the page, press *Pick my squad*, search and add
your fifteen, set your bank and free transfers, save. It is remembered in that
browser and the page recomputes your XI, captain, vice and transfers on the
spot. Nothing to edit, nothing to commit.

**From your FPL team ID.** Pass `--team-id 1234567` (the number in the URL
when you view your team on the FPL site), or set the environment variable
`FPL_TEAM_ID`, and the squad you actually saved for the last deadline is
pulled from FPL. On GitHub, set it once as a repository variable and every
scheduled run uses it.

**A JSON file** (see `my_squad.example.json`):

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

* Each upcoming fixture is turned into a pair of expected-goals numbers,
  60% from **FPL's published fixture difficulty ratings** and 40% from
  **team ratings** (FPL's strengths, updated with this season's results —
  see *Team form* below).
* Every fixture carries two difficulty ratings: the one shown on a side's
  own fixture (how strong its opponent is) and the one shown to the
  opponent (how strong the side itself is). A side's expected goals depend
  on both, and its clean-sheet odds depend on its own fixture's rating —
  that is the opponent's attack. How much each rating is worth in goals was
  fitted on all 760 team-matches of 2024-25 and checked on 2025-26.
* A fixture is always judged against the club's own season: a player's
  base rate was earned against his club's mix of opponents, so only how
  this fixture differs from that mix should move his number.
* FPL does not publish its attack and defence ratings until a season is
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

## Team form

FPL's team ratings change slowly, and in some seasons are not published
until well into the autumn. Results are the evidence they lag behind, so
every club's attack and defence are also rated from this season's matches
(`fpl_model/teamform.py`):

* each match counts **expected goals** (85%) and actual goals (15%) both
  ways — over a handful of games, xG says far more about a side than the
  scoreline does;
* adjusted for the opponent — three goals against the bottom side count
  for less than three against the champions;
* **recent matches count more**: a result's weight halves every ten
  gameweeks;
* **home and away** are tracked separately, on top of the league-wide home
  advantage, and shrunk harder because each rests on half the games;
* blended with FPL's own ratings as if FPL's view were worth eight
  matches, so after two games the ratings have barely moved and by twenty
  they are mostly results.

The dashboard's **Team form** table shows each club's last five results,
its home and away records (goals and xG per game) and the fitted attack and
defence ratings, where 100 is league average.

Scored on what it is for — predicting each club's expected goals in its
next match, using only earlier results — using both difficulty ratings plus
team form lifts the correlation with what happened from 0.37 to 0.45 over
2024-25 and 2025-26, and improves the clean-sheet forecasts too.

## Choosing a formation

By default the XI takes whichever legal shape projects best that week
("auto"), so it can be 5-3-2 one week and 4-4-2 the next. The dashboard's
formation strip lets you fix it instead:

* **Viewing your squad**, picking 3-4-3 fields your fifteen in 3-4-3 every
  week; the captain, vice, bench order and transfer suggestions all follow.
* **Viewing the best £100m squad**, picking 3-4-3 swaps in a squad *built*
  for 3-4-3 — the solver spends the budget on three forwards rather than five
  defenders, and the bench is filled accordingly. *Make this my squad* adopts
  it.

Each button shows what the shape costs against auto over the planning
horizon, and ★ marks the best single formation. Auto always comes out on top
because it is allowed to change shape week to week; a fixed formation
typically costs between half a point and a point and a half a week. On the
command line, `--formation 3-4-3` does the same and prints the comparison
table for all eight.

## How accurate is it?

Every claim above is checked by a **walk-forward backtest**
(`fpl_model/backtest.py`). For each completed gameweek the model is rebuilt
using only what was knowable before that deadline — the per-round log up to
the previous week, the price at the time, the fixtures — and scored against
what actually happened. Nothing from the future leaks in.

Over the whole of 2025-26 (37 scorable gameweeks), with the team ratings
FPL published:

| | Model | Naive season-points pick | Best possible in hindsight |
|---|---|---|---|
| Actual points of the chosen XI, per week (captain doubled) | **61.4** | 50.9 | 155 |
| Weeks the model's XI scored more | **25 of 37** | — | — |
| Captain's actual points, per week | **6.8** | 5.7 | 17.4 |
| Rank correlation, projected vs actual | 0.43 | — | 1.00 |

An edge of about ten points a week over picking on season points,
sustained across most weeks.

One catch: the ratings in that season's files are FPL's *final* ones, which
already reflect how the season went. The live model never has those. So
each change is also replayed the way the live model actually runs — with
only the previous season's overall ratings, as FPL has published for
2026-27 so far — and on a second season, 2024-25. Here is what the fixture
changes (both difficulty ratings plus team form) are worth, in XI points
per week:

| Replay | Before | After |
|---|---|---|
| 2025-26, previous season's ratings (the live situation) | 58.8 | **60.8** |
| 2024-25, previous season's ratings | 69.1 | **69.7** |
| 2025-26, FPL's final ratings (hindsight) | 61.3 | 61.4 |
| 2024-25, FPL's final ratings (hindsight) | 68.9 | 68.8 |

They help where the live model needs help — when FPL's ratings are stale —
and make no difference when the ratings already know the answer.

### Corrections the backtest found

The backtest showed systematic errors that a plain eye test would miss.
The first three fixes below were chosen on the first half of the season and
then checked on the second half, which they had not seen:

* **One keeper per club.** Every keeper's chance of starting was estimated
  on its own, so a club's first and second choice could both look likely to
  play. Keepers were projected at nearly double what they scored. The model
  now hands each club a single keeper slot, first choice first.
* **Club strength was counted twice.** A defender's base rate already
  reflects how good his club is; the fixture adjustment then compared that
  club to the league average and boosted it again. Clean-sheet odds rise
  steeply with defensive strength, so top-club defenders and keepers were
  inflated most. Fixtures are now judged against the club's own typical
  fixture, so only the opponent and venue move the number.
* **Defenders are marked down 20%.** Even after that, defenders delivered
  less than projected relative to attackers, and the model was putting a
  defender in the armband fifteen weeks in thirty-seven. The correction
  (fitted on the first half, confirmed on the second) took that to zero.

Together they added 1.3 points a week to the XI and 1.4 a week to the
captain's score, and turned a +0.18 projection bias into −0.07.

Two more came from the live model putting Raya in the armband in GW6 of
2026-27, while FPL had published only an overall rating for each club:

* **Difficulty ratings counted club strength again.** "A club's typical
  fixture" was modelled as an average opponent at difficulty 3. But FPL
  sets its difficulty ratings from club strength, so Arsenal's opponents
  face a 4 or 5 in every game, and the blended-in rating boosted Arsenal's
  clean-sheet odds every single week. The reference is now the club's
  whole season, run through the same formulas, so the boost averages out.
* **Keepers are marked down 15%.** Among the model's five highest
  projections each week, keepers delivered 76% of what was projected,
  against 104–112% for attackers. A keeper's projection can still be
  right in absolute terms; the correction is about how keepers compare
  with the players they compete with for the armband and the budget.

Replayed with only overall ratings (the mode FPL is in now), these two
added 0.7 points a week to the XI and took keeper captaincy to zero. With
the fixture model since rebuilt around both difficulty ratings and team
form (above), a keeper is captain once in the 37 weeks of 2025-26 — Raya,
at home in GW37, who scored 6.

The default settings in `ModelParams` are the ones the backtest found best.
`tools/tune.py` re-runs the search for any season, which is worth doing each
summer. The dashboard's **"How accurate has it been?"** panel runs the same
replay on *this* season's completed gameweeks every time the page is built,
so you can see for yourself rather than take my word for it.

Caveats, honestly stated: two seasons are still a small sample, and the replay cannot
know who was ruled out on the Friday (the live model can), so it slightly
understates the live model. It also cannot be compared fairly against FPL's
own expected points, which in the public data turn out to be recorded late
enough to know who played.

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
--min-minutes 0         ignore players expected to play fewer minutes per game
--minutes-weight 1.25   how hard to penalise part-players (1.0 is neutral)
--scorecard             replay this season's completed gameweeks, report accuracy
--scorecard-weeks 12    how many recent gameweeks the scorecard covers
--team-id N             pull your saved squad from FPL (or set FPL_TEAM_ID)
--formation 3-4-3       fix the starting shape (default auto: best shape each week)
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
* **Team strength ratings are FPL's own** and they update slowly, which is
  why the model also rates clubs on their results (see *Team form*). When
  FPL's ratings are not published at all the model leans on fixture
  difficulty and form, and says so on the page. Replayed on 2025-26,
  running on the previous season's overall ratings instead of the current
  full ones costs about half a point a week — a small loss, not a broken
  model.
* **The rankings table is not club-capped, and shouldn't be.** It answers "who
  are the best players", not "who can I legally own". The three-per-club rule
  applies to the squad the optimiser builds, which is a different thing.
