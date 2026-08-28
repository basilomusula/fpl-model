# Running the FPL model on Windows

Written for Python **3.11.9**, which you already have. Every command goes into
**PowerShell** (Start → type "PowerShell" → Enter).

Throughout, Python is invoked as `py`. On Windows that is the launcher that
comes with Python and it works even when `python` and `python3` don't. If `py`
ever says it isn't recognised, substitute whichever command printed your
version number.

---

## Step 1 — Extract the zip

Find `fpl-model.zip` in your Downloads folder. Right-click it → **Extract
All…** → **Extract**.

Do not skip this and open the zip by double-clicking. Windows will let you
browse inside a zip as though it were a folder, but nothing can actually run
from in there.

## Step 2 — Move into the folder

```powershell
cd "$env:USERPROFILE\Downloads\fpl-model"
dir
```

You are in the right place if `dir` lists these:

```
fpl_model            (folder)
tests                (folder)
run_fpl.py
README.md
WINDOWS_SETUP.md
requirements.txt
my_squad.example.json
```

**If you only see a single `fpl-model` folder**, Windows nested it. Go one
level deeper and look again:

```powershell
cd fpl-model
dir
```

## Step 3 — Install the four dependencies

```powershell
py -m pip install -r requirements.txt
```

This installs `requests`, `pandas`, `numpy` and `pulp`. It takes a minute and
prints a lot of output; the last line should say `Successfully installed …`.

A yellow warning about upgrading pip is harmless — ignore it.

## Step 4 — First run

```powershell
py run_fpl.py
```

What you should see, in order:

1. `Fetching FPL data...`
2. `fetching 7xx player histories...` followed by a counter ticking up in
   hundreds. **This part takes one to two minutes.** It only happens once —
   the results are cached in a hidden `.fpl_cache` folder, so later runs start
   in a few seconds.
3. The gameweek header, with the GW1 deadline.
4. `TOP 15 PROJECTED PLAYERS` — the model's highest-rated players.
5. `Building the best £100.0m squad...`
6. `STARTING XI` with a formation, then `BENCH`, then the squad cost.

The captain is marked `(C)` and the vice `(V)`. Bench players are numbered in
the order they should be set.

## Step 5 — Open the dashboard

```powershell
start output\fpl_dashboard.html
```

It opens in your default browser. It is a single self-contained file — no
internet needed to view it, and you can move it anywhere or send it to someone.

The `output` folder also holds:

| File | What's in it |
|---|---|
| `player_rankings.csv` | every player, ranked, with the model's working |
| `squad.csv` | the selected 15, in XI-then-bench order |

Both open in Excel.

## Step 6 — Once you've entered a squad on the FPL site

Tell the model what you own so it can pick your XI and suggest transfers.

Open `my_squad.example.json` in Notepad (right-click → Open with → Notepad),
replace the fifteen names with yours, and save it **as `my_squad.json`** in the
same folder. In Notepad's Save As dialog, set *Save as type* to **All Files**,
otherwise it will silently append `.txt`.

```json
{
  "bank": 0.5,
  "free_transfers": 1,
  "players": [
    "Raya", "Dubravka",
    "Gabriel", "Virgil", "Timber", "Muñoz", "Rodon",
    "M.Salah", "Palmer", "Semenyo", "Rogers", "Xavi",
    "Haaland", "Wissa", "Thiago"
  ]
}
```

Use the short names FPL shows on the site. Order does not matter, but you need
exactly fifteen — two goalkeepers, five defenders, five midfielders, three
forwards. Set `bank` to the money you have spare and `free_transfers` to how
many you have this week.

Then:

```powershell
py run_fpl.py --squad my_squad.json
```

Now you get your best XI, bench order, captain and vice, plus a ranked list of
transfers with the −4 hit already subtracted where one applies.

If two players share a name the model stops and lists the options rather than
guessing. Write the club in brackets to settle it — `"Anderson (NFO)"`.

## Step 7 — The weekly routine

Once before the deadline each week:

```powershell
py run_fpl.py --squad my_squad.json --refresh
```

`--refresh` throws away the cached prices and injury news and pulls current
ones. Worth doing on Friday evening, after the press conferences have landed
and FPL has updated its flags.

Remember to keep `my_squad.json` up to date after you make transfers.

---

## If something goes wrong

**`py : The term 'py' is not recognized`**
Use whichever command printed `Python 3.11.9` for you. If nothing does, close
PowerShell and open a fresh window first — PATH changes only apply to new
windows.

**`can't open file 'run_fpl.py': No such file or directory`**
You are in the wrong folder. Re-do Step 2, including the `dir` check.

**`No module named 'pandas'` (or requests, numpy, pulp)**
Step 3 didn't complete, or it installed into a different Python. Run it again
with the exact same `py` you use to run the model:
`py -m pip install -r requirements.txt`

**`pip is not recognized`**
Use `py -m pip` rather than `pip` or `pip3`.

**It hangs on "fetching player histories"**
Give it two minutes. If it is still stuck, Ctrl-C and re-run — completed
downloads are cached, so it picks up where it stopped. On a slow connection,
`py run_fpl.py --top-only 250` fetches history for the 250 most expensive
players only and is much faster.

**`could not fetch bootstrap-static`**
No internet, or a corporate network blocking the FPL site. Try it off VPN.

**Everything ran but `output` is empty**
Look at the last lines of the console output — an error will be printed there.
Paste it to me.

**The top 15 are all from one club**
You are on an old build — update to the latest zip. Separately, note that the
`TOP 15 PROJECTED PLAYERS` list and the rankings table are deliberately not
club-capped; they rank every player in the game. The three-per-club rule
applies to the squad printed under `STARTING XI`.

**Something else**
Copy the whole red error message and send it over.

---

## Getting it on your phone

See **`GITHUB_SETUP.md`** — a scheduled GitHub Action rebuilds the dashboard
and publishes it to a URL you can install on your Android home screen.

---

## Command reference

```powershell
py run_fpl.py                              # best £100m squad from scratch
py run_fpl.py --squad my_squad.json        # your XI, captain and transfers
py run_fpl.py --squad my_squad.json --refresh   # with fresh prices and news
py run_fpl.py --horizon 8                  # plan eight gameweeks ahead
py run_fpl.py --budget 101.5               # a squad worth more than £100m
py run_fpl.py --lock "M.Salah" "Haaland"   # build around players you keep
py run_fpl.py --ban "Haaland"              # exclude someone entirely
py run_fpl.py --top-only 250               # faster, slightly cruder
py run_fpl.py --site _site                 # installable web-app copy
py run_fpl.py --help                       # every option
```
