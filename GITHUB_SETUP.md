# Putting the dashboard on your phone, via GitHub

The end result: a URL that refreshes itself every morning and every Friday
before the deadline, which you install on your Android home screen like an app.
No server, no Play Store, nothing to pay for.

---

## First, the public-vs-private decision

You asked for this before anything gets created, so here it is in full.

**What ends up in the repository either way**

| Thing | Sensitive? |
|---|---|
| The model code | No — it is yours, and there is nothing secret in it |
| `my_squad.json` — your fifteen players | Mildly. See below |
| The generated dashboard | Same as the above |
| Your git commit email address | **Yes, if you use a work address** |

**What never ends up there:** no password, no API key, no FPL login. The FPL
API is completely unauthenticated — the model just reads public endpoints.

**On your squad being visible.** FPL already makes everyone's team public
after each deadline; anyone who knows your team ID can look at it on the
official site. So a public repo does not really expose your XI. What it *does*
expose is your **plans** — the transfer table showing what you are about to do
before you have done it. If you play in a competitive mini-league with people
who might go looking, that is a real if small edge you would be giving away.

**On your email — this is the one I would actually worry about.** Git stamps
your configured email into every commit, and public repos get scraped for
addresses. If you commit as `basil.omusula@onafriq.com`, that work address
becomes permanently harvestable. Fix it before your first commit:

- On github.com: Settings → Emails → tick **Keep my email addresses private**
  and **Block command line pushes that expose my email**.
- Then locally, in the repo folder:
  ```powershell
  git config user.email "YOUR_ID+YOUR_USERNAME@users.noreply.github.com"
  ```
  Your exact noreply address is shown on that same Settings → Emails page.

**The recommendation**

| If | Then |
|---|---|
| You are relaxed about the above | **Public repo.** Simplest, free, Pages works immediately |
| You want it private and free | Private repo, and use **Cloudflare Pages** or **Netlify** instead of GitHub Pages — both build from a private repo on their free tier |
| You want private *and* GitHub Pages | Needs GitHub Pro, about $4/month |

GitHub Pages does not work on private repos on the free plan. That is the only
reason this decision matters at all. Actions minutes are free and unlimited on
public repos, and the free 2,000 minutes/month on private is far more than this
uses (a couple of minutes a day).

This guide assumes **public repo + GitHub Pages**. If you go private, the only
change is Step 5 — point Cloudflare Pages at the repo instead, with build
command `pip install -r requirements.txt && python run_fpl.py --squad
my_squad.json --site _site` and output directory `_site`.

---

## Step 1 — Install Git, if you have not

```powershell
winget install Git.Git
```

Close PowerShell, open a new window, and check:

```powershell
git --version
```

## Step 2 — Create the repository on GitHub

On github.com: **New repository**. Name it `fpl-model`. Public or private per
the decision above. **Do not** tick any of the "initialise with" boxes. Create.

Leave that page open — you will need the URL it shows you.

## Step 3 — Push your folder up

```powershell
cd "$env:USERPROFILE\Downloads\fpl-model"
git init
git branch -M main
git config user.email "YOUR_NOREPLY_ADDRESS"
git config user.name "Bas"
git add .
git commit -m "FPL squad model"
git remote add origin https://github.com/YOUR_USERNAME/fpl-model.git
git push -u origin main
```

Substitute your own username and noreply address. Git will open a browser
window to authenticate the first time.

Note that `.gitignore` keeps `.fpl_cache/` and `output/` out of the repo —
those are rebuilt on every run and would only add noise.

## Step 4 — Add your squad

Make sure `my_squad.json` exists in the folder and is committed:

```powershell
git add my_squad.json
git commit -m "My squad"
git push
```

Without it, the scheduled run builds an optimal squad from scratch instead —
still useful, but you lose the transfer suggestions.

## Step 5 — Turn on Pages

On the repository: **Settings → Pages**. Under *Build and deployment*, set
**Source** to **GitHub Actions**. There is nothing to save; it applies
immediately.

## Step 6 — Run it once by hand

**Actions** tab → **FPL dashboard** in the left sidebar → **Run workflow** →
green button.

It takes two to four minutes the first time, mostly downloading player
histories. When both jobs go green, the deploy step shows your URL:

```
https://YOUR_USERNAME.github.io/fpl-model/
```

## Step 7 — Install it on your phone

Open that URL in **Chrome on your Android phone**. Then either:

- Chrome offers an **Install app** banner — accept it, or
- Menu (⋮) → **Add to Home screen** → **Install**

You get an icon on your home screen. Tapping it opens fullscreen with no
browser chrome, and it works offline from the last version you loaded.

---

## From then on

It rebuilds itself at 08:30 East Africa time daily, and again on Friday
afternoons before the deadline. You do nothing.

**When you make transfers**, update your squad so the model keeps up:

```powershell
cd "$env:USERPROFILE\Downloads\fpl-model"
# edit my_squad.json in Notepad
git add my_squad.json
git commit -m "Transfers for GW5"
git push
```

The push triggers nothing on its own — wait for the next scheduled run, or hit
**Run workflow** to see it immediately.

**Remember to drop `free_transfers` to 1** in `my_squad.json` after the first
deadline. Unlimited changes only apply before the season starts.

---

## If it goes wrong

**The Actions run fails on "Build the dashboard"**
Open the failed step and read the traceback. If it says *could not resolve
these squad entries*, a name in `my_squad.json` is wrong or ambiguous — fix it,
commit, push, re-run.

**`deploy` fails with "Pages not enabled"**
Step 5 was missed. Set Source to *GitHub Actions* and re-run.

**`deploy` fails on a private repo**
Expected — Pages needs a paid plan for private repos. Either make the repo
public or switch to Cloudflare Pages, as described at the top.

**The scheduled run does not fire**
GitHub disables scheduled workflows on repos with no activity for 60 days, and
it emails you first. Any commit re-enables it. Scheduled runs are also queued
rather than punctual — a delay of ten or twenty minutes is normal.

**Chrome will not offer to install it**
It needs HTTPS, which GitHub Pages provides, and it needs to have loaded the
service worker once. Reload the page, then try the menu again.

**The FPL API refuses the connection from GitHub**
Possible but uncommon — FPL occasionally throttles datacentre traffic. The run
will fail with a fetch error. If it happens repeatedly, fall back to running it
on your PC and committing `_site/` by hand.
