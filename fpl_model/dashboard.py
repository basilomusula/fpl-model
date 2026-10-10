"""Self-contained HTML dashboard: pitch view, rankings and transfer ideas.

Everything is inlined - no CDN, no build step, no network access needed to
open the file. Light and dark are both hand-picked palettes rather than one
being an automatic inversion of the other.

Colour carries exactly two meanings here and never anything else: the single
blue series encodes projected points, and the blue ramp on the fixture chips
encodes difficulty. Positions, clubs and verdicts are carried by text, so
nothing depends on colour vision.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

POS_LABEL = {"GK": "Goalkeeper", "DEF": "Defenders",
             "MID": "Midfielders", "FWD": "Forwards"}


def _budget(value) -> str:
    v = float(value or 100.0)
    return f"{v:.0f}m" if v.is_integer() else f"{v:.1f}m"


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _fmt(value, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "–"


# ---------------------------------------------------------------------- #
# small pieces
# ---------------------------------------------------------------------- #

def _fdr_chips(fixtures: list, limit: int = 5) -> str:
    """The upcoming run as difficulty chips: one hue, light easy to dark hard."""
    if not isinstance(fixtures, list) or not fixtures:
        return '<span class="chip chip-none">no fixture</span>'
    out = []
    for f in fixtures[:limit]:
        try:
            fdr = int(f.get("fdr", 3))
            opp = str(f.get("opp", "?"))
            home = bool(f.get("home"))
        except AttributeError:
            continue
        label = ("" if home else "@") + opp.upper()
        out.append(
            f'<span class="chip f{fdr}{"" if home else " away"}" '
            f'title="GW{f.get("gw", "")} {"vs" if home else "away to"} '
            f'{_esc(opp)} · difficulty {fdr}/5 · {_fmt(f.get("ep"), 1)} pts">'
            f'{_esc(label)}<i>{fdr}</i></span>')
    return "".join(out)


def _flag(p: dict) -> str:
    news = str(p.get("news") or "").strip()
    if not news:
        return ""
    try:
        avail = float(p.get("availability", 1) or 1)
    except (TypeError, ValueError):
        avail = 1.0
    if avail <= 0.05:
        cls, glyph, word = "crit", "✕", "Out"
    elif avail < 0.75:
        cls, glyph, word = "warn", "!", "Doubt"
    else:
        cls, glyph, word = "note", "i", "News"
    return (f'<span class="flag {cls}" title="{_esc(news)}">'
            f'<b>{glyph}</b>{word}</span>')


def _card(p: dict, badge: str = "", sub: str = "") -> str:
    return f"""
      <div class="card">
        <div class="card-hd">{badge}{_flag(p)}</div>
        <div class="card-name">{_esc(p['name'])}</div>
        <div class="card-meta">{_esc(p['team_short'])}<span>·</span>£{_fmt(p['price'])}m</div>
        <div class="card-pts"><b>{_fmt(p['ep_next'], 1)}</b><span>pts</span></div>
        <div class="card-fix">{_fdr_chips(p.get('fixture_list'), 3)}</div>
        <div class="card-mins">{_fmt(p.get('xmins'), 0)}' expected</div>
        {f'<div class="card-sub">{sub}</div>' if sub else ''}
      </div>"""


def _pitch(xi: list, captain, vice) -> str:
    rows: dict[str, list] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in xi:
        rows.setdefault(p["pos"], []).append(p)
    for k in rows:
        rows[k].sort(key=lambda x: -x["ep_next"])

    out = []
    for pos in ("GK", "DEF", "MID", "FWD"):
        if not rows[pos]:
            continue
        cards = []
        for p in rows[pos]:
            badge = ""
            if p["id"] == captain:
                badge = '<span class="badge cap" title="Captain — scores double">C</span>'
            elif p["id"] == vice:
                badge = '<span class="badge vice" title="Vice-captain">V</span>'
            cards.append(_card(p, badge))
        out.append(f'<div class="line" data-pos="{POS_LABEL[pos]}">'
                   f'{"".join(cards)}</div>')
    return "".join(out)


def _bench(bench: list, bench_gk) -> str:
    cards = []
    if bench_gk:
        cards.append(_card(bench_gk, '<span class="badge sub">GK</span>'))
    for i, p in enumerate(bench, 1):
        cards.append(_card(p, f'<span class="badge sub" title="Substitute {i}">{i}</span>'))
    return "".join(cards)


def _transfer_rows(transfers: list) -> str:
    if not transfers:
        return ('<tr><td colspan="6" class="empty">Nothing improves this squad '
                'over the horizon — bank the free transfer.</td></tr>')
    out = []
    for t in transfers:
        if t["net_gain"] > 2:
            cls, label = "yes", "Worth it"
        elif t["net_gain"] > 0.5:
            cls, label = "maybe", "Marginal"
        else:
            cls, label = "no", "Hold"
        cost = t["cost"]
        cost_txt = ("level" if abs(cost) < 0.05 else
                    f"−£{_fmt(abs(cost))}m" if cost > 0 else f"+£{_fmt(abs(cost))}m")
        hit = f'<span class="hit">−{t["hit"]} hit</span>' if t["hit"] else ""
        out.append(f"""
        <tr>
          <td class="num dim">{t['moves']}</td>
          <td><span class="swap-out">{_esc(', '.join(t['out_names']))}</span></td>
          <td><span class="swap-in">{_esc(', '.join(t['in_names']))}</span></td>
          <td class="num">{cost_txt}</td>
          <td class="num">{_fmt(t['gain'], 2)} {hit}</td>
          <td class="num"><span class="verdict {cls}">{_fmt(t['net_gain'], 2)}
            <i>{label}</i></span></td>
        </tr>""")
    return "".join(out)


def _ticker(ticker: dict) -> str:
    """Club-by-gameweek difficulty grid, easiest run at the top."""
    gws, rows = ticker.get("gws", []), ticker.get("rows", [])
    if not gws or not rows:
        return ""
    head = "".join(f'<th class="num">GW{g}</th>' for g in gws)
    body = []
    for r in rows:
        cells = []
        for g in gws:
            fixtures = r["cells"].get(str(g), [])
            if not fixtures:
                cells.append('<td class="tick"><span class="chip chip-none">'
                             'blank</span></td>')
                continue
            chips = "".join(
                f'<span class="chip f{f["fdr"]}{"" if f["home"] else " away"}" '
                f'title="{"vs" if f["home"] else "away to"} '
                f'{_esc(f["opp"])} · difficulty {f["fdr"]}/5">'
                f'{_esc(("" if f["home"] else "@") + f["opp"].upper())}'
                f'<i>{f["fdr"]}</i></span>' for f in fixtures)
            cells.append(f'<td class="tick">{chips}</td>')
        avg = r["avg"]
        band = 1 if avg < 2.4 else 2 if avg < 2.9 else 3 if avg < 3.4 else 4 if avg < 3.9 else 5
        body.append(
            f'<tr><td class="strong">{_esc(r["club"])}'
            f'<span class="dim tick-name"> {_esc(r["name"])}</span></td>'
            f'{"".join(cells)}'
            f'<td class="num"><span class="chip f{band}">{_fmt(avg, 2)}</span></td></tr>')
    return f"""
    <div class="tblwrap"><table class="ticker"><thead><tr>
      <th>Club</th>{head}<th class="num">Avg</th>
    </tr></thead><tbody>{"".join(body)}</tbody></table></div>"""


def _form(form: dict | None) -> str:
    """Team form: results, home and away records, and the fitted ratings."""
    rows = (form or {}).get("rows") or []
    if not rows:
        return ""
    xg = bool((form or {}).get("uses_xg"))

    def rec(gf, ga, xgf, xga):
        if gf is None:
            return '<span class="dim">—</span>'
        s = f"{_fmt(gf, 1)}–{_fmt(ga, 1)}"
        if xg and xgf is not None:
            s += f'<span class="dim tick-name"> xG {_fmt(xgf, 1)}–{_fmt(xga, 1)}</span>'
        return s

    def idx(v):
        if v is None:
            return '<span class="dim">—</span>'
        n = round(100 * v)
        band = 1 if n >= 120 else 2 if n >= 107 else 3 if n > 93 else 4 if n > 80 else 5
        return f'<span class="chip f{band}">{n}</span>'

    body = []
    for r in rows:
        last = "".join(
            f'<span class="chip {"f1" if x == "W" else "f3" if x == "D" else "f5"} res">{x}</span>'
            for x in r["last"])
        body.append(f"""
        <tr>
          <td class="strong">{_esc(r['short'].upper())}</td>
          <td class="tick">{last}</td>
          <td class="num">{_fmt(r['ppg'], 2)}</td>
          <td class="num rec">{rec(r['home_gf'], r['home_ga'], r['home_xgf'], r['home_xga'])}</td>
          <td class="num rec">{rec(r['away_gf'], r['away_ga'], r['away_xgf'], r['away_xga'])}</td>
          <td class="num">{idx(r['att'])}</td>
          <td class="num">{idx(r['def'])}</td>
        </tr>""")
    used = (form or {}).get("used", False)
    return f"""
  <div class="panel">
    <h2>Team form</h2>
    <p class="hint">This season so far, most recent result on the right.
      <b>Home</b> and <b>Away</b> are goals
      scored–conceded per game{", with expected goals alongside" if xg else ""}.
      <b>Attack</b> and <b>Defence</b> are the ratings the model fitted from
      these results — 100 is league average, higher is better at both ends —
      adjusted for who each club has played, weighted toward recent games, and
      blended with FPL's own ratings while the sample is small.
      {"They feed every fixture projection, alongside FPL's published difficulty ratings." if used else "Shown for reference; the projections are not using them."}</p>
    <div class="tblwrap"><table class="ticker formtbl"><thead><tr>
      <th>Club</th><th>Last 5</th><th class="num">Pts/game</th>
      <th class="num">Home</th><th class="num">Away</th>
      <th class="num">Attack</th><th class="num">Defence</th>
    </tr></thead><tbody>{"".join(body)}</tbody></table></div>
  </div>"""


def _scorecard(sc: dict | None, horizon_gw: int) -> str:
    """How the model has actually done this season, gameweek by gameweek."""
    if not sc or not sc.get("rows"):
        return ""
    s, rows = sc["summary"], sc["rows"]
    hi = max(max(r["xi_oracle"] for r in rows), 1.0)
    body = []
    for r in rows:
        w = 100.0 * r["xi_model"] / hi
        w_n = 100.0 * r["xi_naive"] / hi
        body.append(f"""
        <tr>
          <td class="num dim">GW{int(r['gw'])}</td>
          <td><div class="sc-track"><div class="sc-fill" style="width:{w:.1f}%"></div>
              <div class="sc-naive" style="left:{w_n:.1f}%" title="naive pick: {_fmt(r['xi_naive'], 0)}"></div></div></td>
          <td class="num strong">{_fmt(r['xi_model'], 0)}</td>
          <td class="num dim">{_fmt(r['xi_naive'], 0)}</td>
          <td class="num dim">{_fmt(r['xi_oracle'], 0)}</td>
          <td class="num">{_fmt(r['captain_model'], 0)}<span class="dim"> / {_fmt(r['captain_best'], 0)}</span></td>
          <td class="num">{_fmt(r['spearman'], 2)}</td>
        </tr>""")
    edge = s["xi_model_mean"] - s["xi_naive_mean"]
    tiles = [
        ("Model XI, per week", _fmt(s["xi_model_mean"], 1), "pts",
         f"{'+' if edge >= 0 else ''}{_fmt(edge, 1)} vs picking on season points"),
        ("Beat the naive pick", f"{s['beats_naive_weeks']} / {s['gameweeks']}", "wks",
         "weeks the model's XI outscored a season-points XI"),
        ("Rank accuracy", _fmt(s["spearman"], 2), "",
         "rank correlation, projected vs actual (1 is perfect)"),
        ("Captain pick", _fmt(s["captain_model_mean"], 1), "pts",
         f"per week · best possible was {_fmt(s['captain_best_mean'], 1)}"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="k">{_esc(k)}</div>'
        f'<div class="v sm">{v}{f"<span class=unit> {u}</span>" if u else ""}</div>'
        f'<div class="n">{_esc(n)}</div></div>' for k, v, u, n in tiles)
    return f"""
  <div class="panel">
    <h2>How accurate has it been?</h2>
    <p class="hint">Each completed gameweek is replayed using only what was
      knowable before its deadline, then scored against what happened.
      <b>Model XI</b> is what the model's best eleven actually scored (captain
      doubled); <b>Naive</b> is an eleven picked purely on season points so
      far; <b>Ceiling</b> is the best eleven in hindsight. Nothing short of a
      crystal ball gets near the ceiling — the number to watch is the gap
      between model and naive.</p>
    <div class="tiles" style="margin-bottom:14px">{tiles_html}</div>
    <div class="tblwrap"><table><thead><tr>
      <th>Week</th><th style="min-width:160px">Model XI <span class="dim">(bar)</span> vs naive <span class="dim">(tick)</span></th>
      <th class="num">Model XI</th><th class="num">Naive</th><th class="num">Ceiling</th>
      <th class="num">Captain / best</th><th class="num">Rank corr</th>
    </tr></thead><tbody>{"".join(body)}</tbody></table></div>
    <p class="legend">Replays cannot know who was injured on the Friday — the
      live model does — so this slightly understates it. Mean absolute error
      {_fmt(s['mae'], 2)} pts per player, bias {'+' if s['bias'] >= 0 else ''}{_fmt(s['bias'], 2)}.</p>
  </div>"""


def _bars(rows: list, n: int = 16) -> str:
    top = rows[:n]
    if not top:
        return ""
    hi = max(r["ep_horizon"] for r in top) or 1.0
    out = []
    for r in top:
        pct = max(2.0, 100.0 * r["ep_horizon"] / hi)
        out.append(f"""
        <div class="bar" tabindex="0"
             data-tip="{_esc(r['name'])} · {_esc(r['team_short'])} · {_esc(r['pos'])} · £{_fmt(r['price'])}m&#10;{_fmt(r['ep_horizon'],1)} projected points&#10;{_fmt(r['ep_next'],1)} next gameweek">
          <div class="bar-name">{_esc(r['name'])}<i>{_esc(r['team_short'])}</i></div>
          <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>
          <div class="bar-num">{_fmt(r['ep_horizon'], 1)}</div>
        </div>""")
    return "".join(out)


# ---------------------------------------------------------------------- #
# styles
# ---------------------------------------------------------------------- #

CSS = """
:root{
  color-scheme:light;
  --page:#f6f6f4; --surface:#fcfcfb; --raised:#ffffff;
  --ink:#0b0b0b; --ink2:#52514e; --dim:#898781;
  --grid:#e8e7e2; --line:#dcdbd4; --ring:rgba(11,11,11,.09);
  --shadow:0 1px 2px rgba(11,11,11,.04),0 8px 24px -12px rgba(11,11,11,.10);
  --shadow-sm:0 1px 2px rgba(11,11,11,.05);
  --series:#2a78d6; --series-ink:#1c5cab;
  --s100:#cde2fb; --s250:#86b6ef; --s400:#3987e5; --s550:#1c5cab; --s700:#0d366b;
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
  --turf-a:#eaf1ea; --turf-b:#e2ebe3; --turf-line:#cfdccf;
  --radius:16px; --radius-sm:11px;
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --page:#0c0c0c; --surface:#1a1a19; --raised:#212120;
  --ink:#ffffff; --ink2:#c3c2b7; --dim:#898781;
  --grid:#2c2c2a; --line:#383835; --ring:rgba(255,255,255,.10);
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
  --shadow-sm:0 1px 2px rgba(0,0,0,.35);
  --series:#3987e5; --series-ink:#86b6ef;
  --s100:#0d366b; --s250:#184f95; --s400:#2a78d6; --s550:#5598e7; --s700:#9ec5f4;
  --turf-a:#171d18; --turf-b:#141a15; --turf-line:#242e25;
}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){
  color-scheme:dark;
  --page:#0c0c0c; --surface:#1a1a19; --raised:#212120;
  --ink:#ffffff; --ink2:#c3c2b7; --dim:#898781;
  --grid:#2c2c2a; --line:#383835; --ring:rgba(255,255,255,.10);
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
  --shadow-sm:0 1px 2px rgba(0,0,0,.35);
  --series:#3987e5; --series-ink:#86b6ef;
  --s100:#0d366b; --s250:#184f95; --s400:#2a78d6; --s550:#5598e7; --s700:#9ec5f4;
  --turf-a:#171d18; --turf-b:#141a15; --turf-line:#242e25;
}}

*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);overflow-x:hidden;
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1200px;margin:0 auto;padding:0 20px 80px}

/* ---- header ---- */
header{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--page) 88%,transparent);
  backdrop-filter:saturate(1.6) blur(10px);border-bottom:1px solid var(--ring);
  margin:0 0 26px;padding:14px 20px}
.hbar{max-width:1160px;margin:0 auto;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:baseline;gap:10px;margin-right:auto;min-width:0}
h1{font-size:17px;font-weight:640;letter-spacing:-.01em;margin:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gw{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;
  color:var(--series-ink);background:color-mix(in srgb,var(--series) 12%,transparent);
  padding:3px 9px;border-radius:999px;white-space:nowrap}
.count{font-size:13px;color:var(--ink2);font-variant-numeric:tabular-nums;white-space:nowrap}
.count b{color:var(--ink);font-weight:640}
.iconbtn{width:34px;height:34px;border-radius:10px;border:1px solid var(--ring);
  background:var(--surface);color:var(--ink2);font-size:15px;cursor:pointer;
  display:grid;place-items:center;box-shadow:var(--shadow-sm)}
.iconbtn:hover{color:var(--ink);border-color:var(--line)}

h2{font-size:15px;font-weight:640;letter-spacing:-.005em;margin:0 0 3px}
.hint{font-size:12.5px;color:var(--dim);margin:0 0 16px;max-width:74ch}
.hint b{color:var(--ink2);font-weight:600}

.panel{background:var(--surface);border:1px solid var(--ring);border-radius:var(--radius);
  padding:22px;margin-bottom:18px;box-shadow:var(--shadow)}

/* ---- stat tiles ---- */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:12px;margin-bottom:18px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:var(--radius);
  padding:15px 17px;box-shadow:var(--shadow)}
.tile .k{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  color:var(--dim)}
.tile .v{font-size:29px;font-weight:660;letter-spacing:-.025em;margin:5px 0 1px;
  line-height:1.05}
.tile .v.sm{font-size:22px}
.tile .n{font-size:12.5px;color:var(--ink2)}

/* ---- pitch ---- */
.pitch{position:relative;border-radius:var(--radius);padding:26px 12px 8px;
  border:1px solid var(--turf-line);overflow:hidden;
  background:
    repeating-linear-gradient(to bottom,var(--turf-a) 0 58px,var(--turf-b) 58px 116px)}
.pitch::before{content:"";position:absolute;inset:10px;z-index:0;
  border:1px solid var(--turf-line);border-radius:9px;pointer-events:none}
.line{position:relative;z-index:1;display:flex;justify-content:center;gap:9px;
  flex-wrap:wrap;margin-bottom:14px;padding-top:15px}
.line::before{content:attr(data-pos);position:absolute;top:0;left:14px;
  font-size:9.5px;font-weight:700;letter-spacing:.10em;text-transform:uppercase;
  color:var(--dim);opacity:.85}
.benchrow{display:flex;justify-content:center;gap:9px;flex-wrap:wrap;margin-top:6px}

.card{position:relative;width:154px;padding:8px 7px 8px;border-radius:var(--radius-sm);
  background:var(--raised);border:1px solid var(--ring);box-shadow:var(--shadow-sm);
  text-align:center;transition:transform .12s ease,box-shadow .12s ease}
.card:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.card-hd{display:flex;justify-content:space-between;align-items:flex-start;
  min-height:18px;gap:4px}
.badge{display:inline-grid;place-items:center;min-width:19px;height:19px;padding:0 5px;
  border-radius:999px;font-size:10.5px;font-weight:700;letter-spacing:.02em}
.badge.cap{background:var(--series);color:#fff}
.badge.vice{background:color-mix(in srgb,var(--series) 22%,transparent);color:var(--series-ink)}
.badge.sub{background:var(--grid);color:var(--ink2)}
.flag{display:inline-flex;align-items:center;gap:3px;margin-left:auto;
  font-size:9.5px;font-weight:650;letter-spacing:.02em;padding:1px 5px 1px 3px;
  border-radius:999px;background:var(--grid);color:var(--ink2)}
.flag b{font-size:9px}
.flag.warn{background:color-mix(in srgb,var(--warn) 26%,transparent);color:var(--ink)}
.flag.crit{background:color-mix(in srgb,var(--crit) 18%,transparent);color:var(--crit)}
.card-name{font-size:13px;font-weight:640;letter-spacing:-.01em;margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card-meta{font-size:10.5px;color:var(--ink2);font-variant-numeric:tabular-nums}
.card-meta span{opacity:.5;margin:0 3px}
.card-pts{margin:3px 0 5px;line-height:1}
.card-pts b{font-size:21px;font-weight:660;letter-spacing:-.03em}
.card-pts span{font-size:9.5px;color:var(--dim);margin-left:3px;font-weight:500}
.card-fix{display:flex;gap:3px;justify-content:space-between;
  flex-wrap:nowrap;align-items:stretch}
.card-fix .chip{flex:0 1 auto;justify-content:center;padding:2px 3px}
.card-sub{font-size:9.5px;color:var(--dim);margin-top:4px}
.card-mins{font-size:9px;color:var(--dim);margin-top:3px;letter-spacing:.01em}

/* ---- fixture difficulty chips ---------------------------------------
   Green (easy) to red (hard), the scale FPL managers already read. Red and
   green are the classic colour-blind failure, so difficulty is carried by
   two further channels that do not depend on hue: lightness falls strictly
   from step 1 to step 5, and the rating itself is printed on every chip.
   A leading @ marks an away fixture.                                      */
.chip{display:inline-flex;align-items:baseline;gap:1px;font-size:9px;
  font-weight:700;letter-spacing:0;padding:2px 4px;border-radius:4px;
  line-height:1.3;white-space:nowrap;border:1px solid rgba(0,0,0,.10)}
.chip i{font-style:normal;font-size:7.5px;font-weight:700;opacity:.85}
.chip.away{letter-spacing:-.01em}
.chip.res{min-width:16px;justify-content:center;margin-right:2px;font-weight:700}
table.formtbl td.rec,table.formtbl td.tick{white-space:nowrap}
.chip.f1{background:#d0edcf;color:#10120f}
.chip.f2{background:#97d496;color:#10120f}
.chip.f3{background:#a8a69f;color:#10120f}
.chip.f4{background:#c1685c;color:#10120f}
.chip.f5{background:#a52e27;color:#ffffff}
:root[data-theme="dark"] .chip{border-color:rgba(255,255,255,.14)}
:root[data-theme="dark"] .chip.f1{background:#57be55;color:#10120f}
:root[data-theme="dark"] .chip.f2{background:#46a744;color:#10120f}
:root[data-theme="dark"] .chip.f3{background:#76736b;color:#ffffff}
:root[data-theme="dark"] .chip.f4{background:#98453a;color:#ffffff}
:root[data-theme="dark"] .chip.f5{background:#8c2721;color:#ffffff}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])) .chip{border-color:rgba(255,255,255,.14)}
  :root:where(:not([data-theme="light"])) .chip.f1{background:#57be55;color:#10120f}
  :root:where(:not([data-theme="light"])) .chip.f2{background:#46a744;color:#10120f}
  :root:where(:not([data-theme="light"])) .chip.f3{background:#76736b;color:#ffffff}
  :root:where(:not([data-theme="light"])) .chip.f4{background:#98453a;color:#ffffff}
  :root:where(:not([data-theme="light"])) .chip.f5{background:#8c2721;color:#ffffff}}
.chip.chip-none{background:var(--grid);color:var(--dim);font-weight:600}
.scale{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--dim);
  margin-top:12px;flex-wrap:wrap}
.scale .chip{font-size:9px}

/* ---- scorecard ---- */
.sc-track{position:relative;height:12px;background:var(--grid);border-radius:5px;overflow:hidden}
.sc-fill{position:absolute;inset:0 auto 0 0;background:var(--series);border-radius:0 4px 4px 0}
.sc-naive{position:absolute;top:-2px;bottom:-2px;width:2px;margin-left:-1px;background:var(--ink2);
  border-radius:1px;z-index:1}

/* ---- bar chart ---- */
.bar{display:grid;grid-template-columns:150px 1fr 48px;align-items:center;gap:12px;
  padding:4px 6px;border-radius:8px;outline:none}
.bar:hover,.bar:focus-visible{background:color-mix(in srgb,var(--series) 7%,transparent)}
.bar-name{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-name i{font-style:normal;color:var(--dim);font-size:11px;margin-left:6px}
.bar-track{height:11px;background:var(--grid);border-radius:5px;overflow:hidden}
.bar-fill{height:100%;background:var(--series);border-radius:0 4px 4px 0}
.bar-num{font-size:12.5px;font-weight:600;font-variant-numeric:tabular-nums;
  color:var(--ink2);text-align:right}

/* ---- tables ---- */
table.ticker td{padding:5px 8px}
table.ticker .tick{white-space:nowrap;width:1%}
table.ticker th:first-child,table.ticker td:first-child{width:auto;max-width:190px}
table.ticker th.num:last-child,table.ticker td.num:last-child{width:1%}
table.ticker .tick .chip{margin-right:2px}
.tick-name{font-size:11px;font-weight:400}
@media (max-width:760px){.tick-name{display:none}}
.tblwrap{overflow:auto;max-height:560px;border-radius:10px;border:1px solid var(--grid)}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:13.5px}
thead th{position:sticky;top:0;z-index:2;background:var(--surface);
  text-align:left;font-weight:600;font-size:12px;letter-spacing:.02em;color:var(--ink2);
  padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th[data-k]{cursor:pointer;user-select:none}
th[data-k]:hover{color:var(--ink)}
th.sorted::after{content:"↓";margin-left:4px;font-size:10px;color:var(--series-ink)}
th.sorted.asc::after{content:"↑"}
td{padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:color-mix(in srgb,var(--series) 5%,transparent)}
.num{text-align:right;font-variant-numeric:tabular-nums}
th.num{text-align:right}
.dim{color:var(--dim)}
.strong{font-weight:640}
.empty{color:var(--dim);text-align:center;padding:26px 10px}
.pos{display:inline-block;min-width:34px;font-size:10.5px;font-weight:700;
  letter-spacing:.04em;color:var(--ink2);background:var(--grid);
  padding:2px 6px;border-radius:5px;text-align:center}
.swap-out{color:var(--crit);font-weight:560}
.swap-in{color:var(--good);font-weight:600}
.hit{font-size:11px;color:var(--dim);white-space:nowrap}
.verdict{display:inline-flex;align-items:baseline;gap:6px;padding:3px 9px;
  border-radius:999px;font-size:12.5px;font-weight:650;font-variant-numeric:tabular-nums;
  background:var(--grid);color:var(--ink2)}
.verdict i{font-style:normal;font-size:11px;font-weight:600}
.verdict.yes{background:color-mix(in srgb,var(--good) 16%,transparent);color:var(--good)}
.verdict.maybe{background:color-mix(in srgb,var(--warn) 26%,transparent);color:var(--ink)}

/* ---- controls ---- */
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
input,select{font:inherit;font-size:13.5px;padding:7px 11px;border-radius:9px;
  border:1px solid var(--line);background:var(--raised);color:var(--ink);
  box-shadow:var(--shadow-sm)}
input:focus,select:focus{outline:2px solid color-mix(in srgb,var(--series) 45%,transparent);
  outline-offset:1px;border-color:var(--series)}
.seg{display:inline-flex;background:var(--grid);border-radius:9px;padding:2px;gap:2px}
.seg button{font:inherit;font-size:12.5px;font-weight:600;border:0;cursor:pointer;
  padding:5px 11px;border-radius:7px;background:transparent;color:var(--ink2)}
.seg button[aria-pressed="true"]{background:var(--raised);color:var(--ink);
  box-shadow:var(--shadow-sm)}
.mini{font-size:12px;color:var(--dim)}


/* ---- squad picker ---- */
.squadbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px;
  padding:10px 12px;border-radius:11px;background:color-mix(in srgb,var(--series) 7%,transparent);
  border:1px solid color-mix(in srgb,var(--series) 22%,transparent);font-size:13px}
.squadbar .grow{flex:1;min-width:220px}
.btn{font:inherit;font-size:12.5px;font-weight:600;padding:6px 11px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:var(--raised);color:var(--ink);box-shadow:var(--shadow-sm)}
.btn.primary{background:var(--series);border-color:var(--series);color:#fff}
.btn:hover{filter:brightness(1.04)}
.btn[disabled]{opacity:.45;cursor:not-allowed}
#editor{margin-bottom:16px;border:1px solid var(--ring);border-radius:var(--radius);
  background:var(--surface);box-shadow:var(--shadow);padding:16px}
#editor[hidden]{display:none}
.ed-cols{display:grid;grid-template-columns:1.1fr .9fr;gap:16px}
@media (max-width:760px){.ed-cols{grid-template-columns:1fr}}
.ed-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.ed-scroll{max-height:380px;overflow:auto;border:1px solid var(--grid);border-radius:9px}
.ed-row{display:flex;gap:8px;align-items:center;padding:6px 9px;border-bottom:1px solid var(--grid);font-size:13px}
.ed-row:last-child{border-bottom:0}
.ed-row b{font-weight:620}
.ed-row .dim{margin-left:auto;font-size:12px;white-space:nowrap}
.ed-row button{font:inherit;font-size:12px;font-weight:600;padding:3px 9px;border-radius:7px;cursor:pointer;
  border:1px solid var(--line);background:var(--raised);color:var(--ink)}
.ed-row button[disabled]{opacity:.4;cursor:not-allowed;font-weight:500}
.ed-row button.rm{border-color:transparent;color:var(--crit);background:transparent;padding:2px 6px}
.ed-group{margin-bottom:8px}
.ed-gh{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);
  padding:6px 9px 4px;display:flex;justify-content:space-between}
.ed-foot{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px;padding-top:12px;
  border-top:1px solid var(--grid);font-size:13px}
.ed-foot label{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--ink2)}
.ed-foot input{width:78px}
.unit{font-size:13px;color:var(--dim);font-weight:500}

/* ---- formation picker ---- */
.formbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 10px}
.formbar-k{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
.forms{display:flex;gap:6px;flex-wrap:wrap}
.forms button{font:inherit;display:inline-flex;flex-direction:column;align-items:center;gap:1px;
  min-width:64px;padding:5px 9px 4px;border-radius:9px;cursor:pointer;
  border:1px solid var(--line);background:var(--raised);color:var(--ink);box-shadow:var(--shadow-sm)}
.forms button b{font-size:13px;font-weight:650;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.forms button span{font-size:10.5px;color:var(--dim);font-variant-numeric:tabular-nums}
.forms button{position:relative}
.forms button b{white-space:nowrap}
.forms button .star{position:absolute;top:1px;right:4px;font-style:normal;font-size:9px;
  line-height:1;color:var(--series-ink)}
.forms button[aria-pressed="true"]{border-color:var(--series);background:var(--series);color:#fff}
.forms button[aria-pressed="true"] span,.forms button[aria-pressed="true"] .star{color:rgba(255,255,255,.85)}
.forms button[disabled]{opacity:.4;cursor:not-allowed}
.forms button:not([aria-pressed="true"]):not([disabled]):hover{border-color:var(--series)}
.seg button[disabled]{opacity:.45;cursor:not-allowed}
@media (max-width:640px){
  .formbar{flex-direction:column;align-items:stretch;gap:6px}
  .forms{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:5px}
  .forms button{min-width:0;padding:4px 2px 3px}
  .forms button span{font-size:9.5px}
}

/* ---- misc ---- */
.notice{display:flex;gap:9px;align-items:flex-start;font-size:12.5px;color:var(--ink2);
  background:color-mix(in srgb,var(--warn) 12%,transparent);
  border:1px solid color-mix(in srgb,var(--warn) 30%,transparent);
  border-radius:11px;padding:11px 13px;margin-bottom:18px}
.notice b{color:var(--ink)}
footer{color:var(--dim);font-size:12px;line-height:1.7;margin-top:26px;
  padding-top:18px;border-top:1px solid var(--grid)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media (max-width:900px){.cols{grid-template-columns:1fr}}
@media (max-width:640px){
  .card{width:112px}
  .card-fix .chip:nth-child(n+3){display:none}
  .bar{grid-template-columns:112px 1fr 42px;gap:9px}
  .panel{padding:16px}
  .tile .v{font-size:25px}
}
@media print{header{position:static}.panel{box-shadow:none;break-inside:avoid}}
"""

JS = """
const RANKS = __RANKS__, DEADLINE = "__DEADLINE__";
const el = id => document.getElementById(id);
let sortKey = "ep_horizon", sortDir = -1;

function chips(list){
  if(!Array.isArray(list) || !list.length) return '<span class="chip chip-none">–</span>';
  return list.slice(0,5).map(f => {
    const tag = (f.home ? "" : "@") + String(f.opp).toUpperCase();
    return `<span class="chip f${f.fdr}${f.home ? "" : " away"}" title="GW${f.gw} ${
      f.home ? "vs" : "away to"} ${f.opp} · difficulty ${f.fdr}/5">`
         + `${tag}<i>${f.fdr}</i></span>`;
  }).join("");
}

function fdrBadge(v){
  if(v === null || v === undefined || isNaN(v)) return '<span class="dim">–</span>';
  const b = v < 2.4 ? 1 : v < 2.9 ? 2 : v < 3.4 ? 3 : v < 3.9 ? 4 : 5;
  return `<span class="chip f${b}">${Number(v).toFixed(2)}</span>`;
}

function render(){
  const q = (el("q").value || "").toLowerCase();
  const pos = document.querySelector('.seg button[aria-pressed="true"]').dataset.pos;
  const maxp = parseFloat(el("maxp").value || "99");
  const minm = parseFloat(el("minmins").value || "0");
  let rows = RANKS.filter(r =>
    (!q || r.name.toLowerCase().includes(q) || r.team_short.toLowerCase().includes(q))
    && (pos === "ALL" || r.pos === pos) && r.price <= maxp
    && (r.xmins || 0) >= minm);
  rows.sort((a,b) => {
    const x = a[sortKey], y = b[sortKey];
    return (typeof x === "string") ? sortDir * x.localeCompare(y) : sortDir * (x - y);
  });
  el("count").textContent = rows.length + (rows.length === 1 ? " player" : " players");
  el("rank-body").innerHTML = rows.slice(0,250).map((r,i) => `<tr>
    <td class="num dim">${i+1}</td>
    <td class="strong">${r.name}${r.news ? ` <span class="flag" title="${
      String(r.news).replace(/"/g,"&quot;")}"><b>i</b></span>` : ""}</td>
    <td><span class="pos">${r.pos}</span></td>
    <td class="dim">${r.team_short}</td>
    <td class="num">${r.price.toFixed(1)}</td>
    <td class="num strong">${r.ep_next.toFixed(2)}</td>
    <td class="num">${r.ep_horizon.toFixed(2)}</td>
    <td class="num">${r.value.toFixed(2)}</td>
    <td class="num dim">${Math.round(r.xmins)}</td>
    <td class="num">${Math.round(r.curr_minutes || 0)}</td>
    <td class="num dim">${Math.round((r.p_start || 0) * 100)}%</td>
    <td class="num">${fdrBadge(r.mean_fdr)}</td>
    <td>${chips(r.fixture_list)}</td></tr>`).join("")
    || '<tr><td colspan="13" class="empty">No players match those filters.</td></tr>';
  document.querySelectorAll("th[data-k]").forEach(th => {
    th.classList.toggle("sorted", th.dataset.k === sortKey);
    th.classList.toggle("asc", th.dataset.k === sortKey && sortDir === 1);
  });
}

document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
  const k = th.dataset.k;
  sortDir = (k === sortKey) ? -sortDir : -1;
  sortKey = k; render();
});
document.querySelectorAll('.seg button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.seg button').forEach(o =>
    o.setAttribute("aria-pressed", o === b ? "true" : "false"));
  render();
});
["q","maxp","minmins"].forEach(id => el(id).oninput = render);

el("theme").onclick = () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const next = cur === "dark" ? "light" : cur === "light" ? "dark" : (dark ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", next);
};

document.querySelectorAll(".bar").forEach(b => b.title = b.dataset.tip);

(function countdown(){
  const node = el("count-down");
  if(!node || !DEADLINE) return;
  const target = new Date(DEADLINE).getTime();
  const tick = () => {
    const diff = target - Date.now();
    if(!isFinite(diff)) { node.textContent = ""; return; }
    if(diff <= 0){ node.innerHTML = "deadline passed"; return; }
    const d = Math.floor(diff/864e5), h = Math.floor(diff/36e5) % 24,
          m = Math.floor(diff/6e4) % 60;
    node.innerHTML = "deadline in <b>" + (d ? d + "d " : "") + h + "h " + m + "m</b>";
  };
  tick(); setInterval(tick, 30000);
})();

render();

/* ==================== in-page squad picker ============================
   Everything below runs in the browser against the projections the build
   embedded. Picking the XI, the captain and the vice, and scoring transfer
   options, are all cheap enough to do live - the heavy lifting (projecting
   700 players) was done once by the Python build.                        */
const PLAYERS = __PLAYERS__, GWS = __GWS__;
const INITIAL_SQUAD = __INITIAL_SQUAD__, MODEL_SQUADS = __MODEL_SQUADS__;
const BANK0 = __BANK0__, FT0 = __FT0__, FORMATION0 = "__FORMATION0__", BUDGET = __BUDGET__;
const DECAY = 0.88;
const BUDGET_TXT = "£" + (Number.isInteger(BUDGET) ? BUDGET : BUDGET.toFixed(1)) + "m";
const P = Object.fromEntries(PLAYERS.map(p => [p.id, p]));
const FORMS = [[3,4,3],[3,5,2],[4,3,3],[4,4,2],[4,5,1],[5,2,3],[5,3,2],[5,4,1]];
const LIMITS = {GK:2, DEF:5, MID:5, FWD:3};
const POSNAME = {GK:"Goalkeeper", DEF:"Defenders", MID:"Midfielders", FWD:"Forwards"};
const STORE = "fpl-squad-v1", UI_STORE = "fpl-ui-v1";
const FORM_KEYS = ["auto", ...FORMS.map(f => f.join("-"))];
const shapeOf = k => (!k || k === "auto") ? null : k.split("-").map(Number);
const WEEK_WEIGHT = GWS.reduce((s, _, i) => s + DECAY ** i, 0) || 1;

const ep = (id, gw) => (P[id] && P[id].ep[String(gw)]) || 0;
const money = v => "£" + Number(v).toFixed(1) + "m";

function loadState(){
  try { const s = JSON.parse(localStorage.getItem(STORE) || "null");
        if (s && Array.isArray(s.squad) && s.squad.length === 15
            && s.squad.every(id => P[id])) return s; } catch(e){}
  if (INITIAL_SQUAD.length === 15) return {squad: INITIAL_SQUAD.slice(), bank: BANK0, ft: FT0, source: "fpl"};
  return null;
}
function saveState(s){ try { localStorage.setItem(STORE, JSON.stringify(s)); } catch(e){} }
function clearState(){ try { localStorage.removeItem(STORE); } catch(e){} }
function loadUI(){
  let u = {};
  try { u = JSON.parse(localStorage.getItem(UI_STORE) || "{}") || {}; } catch(e){}
  if (!FORM_KEYS.includes(u.formation)) u.formation = FORM_KEYS.includes(FORMATION0) ? FORMATION0 : "auto";
  if (u.view !== "mine" && u.view !== "model") u.view = loadState() ? "mine" : "model";
  if (u.view === "mine" && !loadState()) u.view = "model";
  return u;
}
function saveUI(u){ try { localStorage.setItem(UI_STORE, JSON.stringify(u)); } catch(e){} }

function bestXI(ids, gw, shape){
  const by = {GK:[], DEF:[], MID:[], FWD:[]};
  ids.forEach(id => by[P[id].pos].push(id));
  for (const k in by) by[k].sort((a,b) => ep(b,gw) - ep(a,gw));
  if (!by.GK.length) return null;
  const gk = by.GK[0], benchGk = by.GK[1] || null;
  let best = null, bestPts = -1e9, bestShape = null;
  for (const [d,m,f] of (shape ? [shape] : FORMS)){
    if (by.DEF.length < d || by.MID.length < m || by.FWD.length < f) continue;
    const pick = [...by.DEF.slice(0,d), ...by.MID.slice(0,m), ...by.FWD.slice(0,f)];
    const pts = ep(gk,gw) + pick.reduce((s,id) => s + ep(id,gw), 0);
    if (pts > bestPts){ best = pick; bestPts = pts; bestShape = [d,m,f]; }
  }
  if (!best) return null;
  const xi = [gk, ...best];
  const inXI = new Set(xi);
  const bench = ids.filter(id => !inXI.has(id) && id !== benchGk)
                   .sort((a,b) => ep(b,gw) - ep(a,gw));
  const sorted = xi.slice().sort((a,b) => ep(b,gw) - ep(a,gw));
  const captain = sorted[0];
  const rel = id => (P[id].p_app || 1) * (P[id].availability ?? 1);
  const vice = xi.filter(id => id !== captain)
                 .sort((a,b) => ep(b,gw)*rel(b)**2 - ep(a,gw)*rel(a)**2)[0] || null;
  return {xi, bench, benchGk, pts: bestPts, formation: bestShape.join("-"), captain, vice};
}

function horizonPts(ids, shape){
  let total = 0;
  GWS.forEach((gw, i) => {
    const line = bestXI(ids, gw, shape); if (!line) return;
    total += (line.pts + ep(line.captain, gw)) * DECAY ** i;
  });
  return total;
}

function clubCounts(ids){ const c = {}; ids.forEach(id => { c[P[id].team] = (c[P[id].team]||0) + 1; }); return c; }
function legal(ids){
  const pos = {GK:0, DEF:0, MID:0, FWD:0};
  ids.forEach(id => pos[P[id].pos]++);
  const over = Object.values(clubCounts(ids)).some(n => n > 3);
  const probs = [];
  for (const k in LIMITS) if (pos[k] !== LIMITS[k]) probs.push(`${pos[k]}/${LIMITS[k]} ${k}`);
  if (over) probs.push("more than 3 from one club");
  return {ok: ids.length === 15 && !probs.length, problems: probs, pos};
}

function suggestTransfers(ids, bank, ft, shape){
  const base = horizonPts(ids, shape);
  const owned = new Set(ids), counts = clubCounts(ids);
  const maxOwned = Math.max(...ids.map(id => P[id].price));
  const cands = {GK:[], DEF:[], MID:[], FWD:[]};
  PLAYERS.filter(p => !owned.has(p.id) && p.availability >= 0.75 && p.xmins >= 1
                   && p.price <= bank + maxOwned + 0.05)
         .sort((a,b) => b.ep_weighted - a.ep_weighted)
         .forEach(p => { if (cands[p.pos].length < 40) cands[p.pos].push(p.id); });
  const singles = [];
  for (const out of ids){
    for (const inn of cands[P[out].pos]){
      const cost = P[inn].price - P[out].price;
      if (cost > bank + 1e-9) continue;
      if (P[inn].team !== P[out].team && (counts[P[inn].team]||0) + 1 > 3) continue;
      const next = ids.map(id => id === out ? inn : id);
      const gain = horizonPts(next, shape) - base;
      const hit = ft >= 1 ? 0 : 4;
      singles.push({moves:1, out:[out], inn:[inn], cost, gain, hit, net: gain - hit});
    }
  }
  singles.sort((a,b) => b.gain - a.gain);
  const results = singles.slice();
  const short = singles.slice(0, 14);
  for (let i = 0; i < short.length; i++) for (let j = i+1; j < short.length; j++){
    const a = short[i], b = short[j];
    if (a.out[0] === b.out[0] || a.inn[0] === b.inn[0]) continue;
    const cost = a.cost + b.cost; if (cost > bank + 1e-9) continue;
    const next = ids.filter(id => id !== a.out[0] && id !== b.out[0]).concat([a.inn[0], b.inn[0]]);
    if (Object.values(clubCounts(next)).some(n => n > 3)) continue;
    const gain = horizonPts(next, shape) - base, hit = Math.max(0, 2 - ft) * 4;
    results.push({moves:2, out:[a.out[0], b.out[0]], inn:[a.inn[0], b.inn[0]], cost, gain, hit, net: gain - hit});
  }
  results.sort((a,b) => b.net - a.net || a.cost - b.cost);
  const seen = new Set(), perSeller = {}, out = [];
  for (const r of results){
    const key = r.inn.slice().sort().join(","), seller = r.out.slice().sort().join(",");
    if (seen.has(key) || r.gain <= 0.25) continue;
    if ((perSeller[seller]||0) >= 4) continue;
    seen.add(key); perSeller[seller] = (perSeller[seller]||0) + 1;
    out.push(r); if (out.length >= 12) break;
  }
  return {base, list: out};
}

/* ---- rendering ---------------------------------------------------- */
function flagHTML(p){
  if (!p.news) return "";
  const a = p.availability ?? 1;
  const [cls, g, w] = a <= 0.05 ? ["crit","✕","Out"] : a < 0.75 ? ["warn","!","Doubt"] : ["note","i","News"];
  return `<span class="flag ${cls}" title="${String(p.news).replace(/"/g,"&quot;")}"><b>${g}</b>${w}</span>`;
}
function cardHTML(id, gw, badge){
  const p = P[id];
  return `<div class="card">
    <div class="card-hd">${badge||""}${flagHTML(p)}</div>
    <div class="card-name">${p.name}</div>
    <div class="card-meta">${p.team_short}<span>·</span>${money(p.price)}</div>
    <div class="card-pts"><b>${ep(id,gw).toFixed(1)}</b><span>pts</span></div>
    <div class="card-fix">${chips((p.fixture_list||[]).slice(0,3))}</div>
    <div class="card-mins">${Math.round(p.xmins)}' expected</div></div>`;
}
function activeSquad(ui){
  if (ui.view === "mine"){ const s = loadState(); if (s) return s; }
  const k = MODEL_SQUADS[ui.formation] ? ui.formation : "auto";
  const ms = MODEL_SQUADS[k];
  return ms ? {squad: ms.squad.slice(), bank: ms.bank, ft: FT0, source: "model", key: k} : null;
}

function renderFormations(ui){
  const mine = loadState();
  const pts = {};
  for (const k of FORM_KEYS){
    const ids = ui.view === "mine" ? (mine && mine.squad)
                                   : (MODEL_SQUADS[k] && MODEL_SQUADS[k].squad);
    pts[k] = ids ? horizonPts(ids, shapeOf(k)) : null;
  }
  const fixed = FORM_KEYS.slice(1).filter(k => pts[k] !== null);
  const bestFixed = fixed.reduce((a, k) => (a === null || pts[k] > pts[a]) ? k : a, null);
  el("forms").innerHTML = FORM_KEYS.map(k => {
    const v = pts[k], on = k === ui.formation;
    const gap = (v !== null && pts.auto !== null) ? v - pts.auto : null;
    const sub = v === null ? "n/a" : k === "auto" ? `${v.toFixed(0)} · best` :
      (Math.abs(gap) < 0.05 ? "= auto" : `${gap > 0 ? "+" : "−"}${Math.abs(gap).toFixed(1)}`);
    const star = k === bestFixed ? '<i class="star" title="best fixed formation">★</i>' : "";
    return `<button data-f="${k}" aria-pressed="${on}" ${v === null ? "disabled" : ""}
      title="${k === "auto" ? "Field whichever shape projects best each week" : "Field " + k + " every week"}">
      <b>${k === "auto" ? "Auto" : k}${star}</b><span>${sub}</span></button>`;
  }).join("");

  const v = pts[ui.formation], gap = (v !== null && pts.auto !== null) ? pts.auto - v : 0;
  const perWeek = gap / WEEK_WEIGHT;
  let hint;
  if (ui.formation === "auto"){
    hint = ui.view === "mine"
      ? `Your XI takes whichever shape projects best each week. The best single formation for your squad is <b>${bestFixed}</b>.`
      : `The best ${BUDGET_TXT} squad when the shape is free to change week to week. The best single formation is <b>${bestFixed}</b>.`;
  } else if (gap < 0.05){
    hint = `<b>${ui.formation}</b> costs nothing here — it is as good as letting the shape change.`;
  } else {
    hint = (ui.view === "mine"
      ? `Fielding your squad in <b>${ui.formation}</b> every week`
      : `The best ${BUDGET_TXT} squad built for <b>${ui.formation}</b>`)
      + ` projects <b>${gap.toFixed(1)} pts</b> below auto over the next ${GWS.length} gameweeks — about ${perWeek.toFixed(1)} a week. A fair price if you prefer the shape.`;
  }
  el("form-hint").innerHTML = hint + ' <span class="dim">Figures: projected points over the next ' + GWS.length + ' gameweeks, captain included, relative to auto.</span>';
}

function renderActive(){
  const ui = loadUI();
  const mine = loadState();
  document.querySelectorAll("#view-seg button").forEach(b => {
    b.setAttribute("aria-pressed", b.dataset.view === ui.view ? "true" : "false");
    if (b.dataset.view === "mine"){
      b.disabled = !mine;
      b.title = mine ? "Your squad" : "Pick your squad first";
    }
  });
  renderFormations(ui);
  const state = activeSquad(ui);
  if (!state) return;
  renderSquad(state, shapeOf(ui.formation), ui);
}

function renderSquad(state, shape, ui){
  const gw = GWS[0], line = bestXI(state.squad, gw, shape);
  if (!line) return;
  el("xi-shape").textContent = "· " + line.formation + (shape ? "" : " (auto)");
  const rows = {GK:[], DEF:[], MID:[], FWD:[]};
  line.xi.forEach(id => rows[P[id].pos].push(id));
  let pitch = "";
  for (const pos of ["GK","DEF","MID","FWD"]){
    if (!rows[pos].length) continue;
    rows[pos].sort((a,b) => ep(b,gw) - ep(a,gw));
    pitch += `<div class="line" data-pos="${POSNAME[pos]}">` + rows[pos].map(id => cardHTML(id, gw,
      id === line.captain ? '<span class="badge cap" title="Captain">C</span>' :
      id === line.vice ? '<span class="badge vice" title="Vice-captain">V</span>' : "")).join("") + "</div>";
  }
  el("pitch").innerHTML = pitch;
  el("bench").innerHTML = (line.benchGk ? cardHTML(line.benchGk, gw, '<span class="badge sub">GK</span>') : "")
    + line.bench.map((id,i) => cardHTML(id, gw, `<span class="badge sub">${i+1}</span>`)).join("");

  const cost = state.squad.reduce((s,id) => s + P[id].price, 0);
  const hp = horizonPts(state.squad, shape);
  const rel = id => (P[id].p_app||1) * (P[id].availability ?? 1);
  el("t-xi").innerHTML = `${line.pts.toFixed(1)}<span class="unit"> pts</span>`;
  el("t-xi-n").textContent = `${line.formation} · captain doubles on top`;
  el("t-cap").textContent = P[line.captain].name;
  el("t-cap-n").textContent = `${ep(line.captain,gw).toFixed(1)} → ${(2*ep(line.captain,gw)).toFixed(1)} pts doubled`;
  el("t-vc").textContent = line.vice ? P[line.vice].name : "–";
  el("t-vc-n").textContent = line.vice ? `${ep(line.vice,gw).toFixed(1)} pts · ${Math.round(100*rel(line.vice))}% sure to play · takes the armband if the captain does not feature` : "";
  el("t-val").textContent = money(cost);
  el("t-val-n").textContent = `${money(state.bank)} in the bank`;
  el("t-hor").innerHTML = `${hp.toFixed(0)}<span class="unit"> pts</span>`;

  const isModel = state.source === "model";
  el("btn-adopt").hidden = !isModel;
  if (isModel){
    el("transfer-body").innerHTML = `<tr><td colspan="6" class="empty">This is the model&rsquo;s own best squad, so there is nothing to transfer. Switch to <b>My squad</b> for suggestions on yours.</td></tr>`;
    const label = state.key === "auto" ? "shape free to change" : state.key;
    el("squad-status").innerHTML = `<b>Best ${BUDGET_TXT} squad</b> · ${label} · ${money(cost)} · ${money(state.bank)} left <span class="dim">· the model's pick</span>`;
    return;
  }

  const tr = suggestTransfers(state.squad, state.bank, state.ft, shape);
  el("transfer-body").innerHTML = tr.list.length ? tr.list.map(r => {
    const [cls, label] = r.net > 2 ? ["yes","Worth it"] : r.net > 0.5 ? ["maybe","Marginal"] : ["no","Hold"];
    const costTxt = Math.abs(r.cost) < 0.05 ? "level" : (r.cost > 0 ? "−" : "+") + money(Math.abs(r.cost));
    return `<tr><td class="num dim">${r.moves}</td>
      <td><span class="swap-out">${r.out.map(id => P[id].name).join(", ")}</span></td>
      <td><span class="swap-in">${r.inn.map(id => P[id].name).join(", ")}</span></td>
      <td class="num">${costTxt}</td>
      <td class="num">${r.gain.toFixed(2)} ${r.hit ? `<span class="hit">−${r.hit} hit</span>` : ""}</td>
      <td class="num"><span class="verdict ${cls}">${r.net.toFixed(2)} <i>${label}</i></span></td></tr>`;
  }).join("") : '<tr><td colspan="6" class="empty">Nothing improves this squad over the horizon — bank the free transfer.</td></tr>';

  const src = state.source === "fpl" ? "loaded from your FPL team" : "saved in this browser";
  el("squad-status").innerHTML = `<b>Your squad</b> · ${money(cost)} · ${money(state.bank)} in the bank · ${state.ft} free transfer${state.ft===1?"":"s"} <span class="dim">· ${src}</span>`;
}

/* ---- editor ------------------------------------------------------- */
let draft = null;
function openEditor(fromIds, bank, ft){
  draft = {squad: (fromIds||[]).slice(), bank: bank ?? 0, ft: ft ?? 1};
  el("editor").hidden = false; el("ed-bank").value = draft.bank; el("ed-ft").value = draft.ft;
  el("ed-q").value = ""; renderEditor(); el("ed-q").focus();
}
function renderEditor(){
  const q = el("ed-q").value.toLowerCase();
  const pos = document.querySelector("#ed-pos button[aria-pressed=true]").dataset.pos;
  const owned = new Set(draft.squad), counts = clubCounts(draft.squad), chk = legal(draft.squad);
  const list = PLAYERS.filter(p => !owned.has(p.id) && (pos === "ALL" || p.pos === pos)
      && (!q || p.name.toLowerCase().includes(q) || p.team_short.toLowerCase().includes(q)))
    .sort((a,b) => b.ep_horizon - a.ep_horizon).slice(0, 60);
  el("ed-list").innerHTML = list.map(p => {
    const full = chk.pos[p.pos] >= LIMITS[p.pos], club = (counts[p.team]||0) >= 3;
    const why = full ? `${p.pos} full` : club ? "3 from club" : "";
    return `<div class="ed-row"><span class="pos">${p.pos}</span><b>${p.name}</b>
      <span class="dim">${p.team_short} · ${money(p.price)} · ${p.ep_next.toFixed(1)} pts</span>
      <button data-add="${p.id}" ${why?"disabled":""} title="${why}">${why || "Add"}</button></div>`;
  }).join("") || '<div class="empty">No players match.</div>';
  const groups = {GK:[], DEF:[], MID:[], FWD:[]};
  draft.squad.forEach(id => groups[P[id].pos].push(id));
  el("ed-squad").innerHTML = Object.keys(groups).map(k => `<div class="ed-group"><div class="ed-gh">${POSNAME[k]}
      <span class="dim">${groups[k].length}/${LIMITS[k]}</span></div>` +
    groups[k].sort((a,b) => P[b].ep_next - P[a].ep_next).map(id => `<div class="ed-row"><b>${P[id].name}</b>
      <span class="dim">${P[id].team_short} · ${money(P[id].price)}</span>
      <button data-rm="${id}" class="rm" title="Remove">✕</button></div>`).join("") + "</div>").join("");
  const cost = draft.squad.reduce((s,id) => s + P[id].price, 0);
  el("ed-sum").innerHTML = `<b>${draft.squad.length}/15</b> · ${money(cost)}` +
    (chk.ok ? ' <span class="verdict yes">Ready to save</span>' : ` <span class="dim">· needs: ${chk.problems.join(", ") || "15 players"}</span>`);
  el("ed-save").disabled = !chk.ok;
}
function wirePicker(){
  renderActive();

  el("view-seg").onclick = e => {
    const b = e.target.closest("button[data-view]"); if (!b || b.disabled) return;
    const u = loadUI(); u.view = b.dataset.view; saveUI(u); renderActive();
  };
  el("forms").onclick = e => {
    const b = e.target.closest("button[data-f]"); if (!b || b.disabled) return;
    const u = loadUI(); u.formation = b.dataset.f; saveUI(u); renderActive();
  };
  el("btn-pick").onclick = () => { const s = loadState(); openEditor(s ? s.squad : [], s ? s.bank : BANK0, s ? s.ft : FT0); };
  el("btn-adopt").onclick = () => {
    const u = loadUI(), k = MODEL_SQUADS[u.formation] ? u.formation : "auto", s = loadState();
    openEditor(MODEL_SQUADS[k].squad, MODEL_SQUADS[k].bank, s ? s.ft : FT0);
  };
  el("btn-clear").onclick = () => { clearState(); const u = loadUI(); u.view = "model"; saveUI(u); location.reload(); };
  el("ed-cancel").onclick = () => { el("editor").hidden = true; };
  el("ed-save").onclick = () => {
    const s = {squad: draft.squad.slice(), bank: parseFloat(el("ed-bank").value||"0"), ft: parseInt(el("ed-ft").value||"1",10), source: "local"};
    saveState(s); const u = loadUI(); u.view = "mine"; saveUI(u);
    el("editor").hidden = true; renderActive();
    el("squad-panel").scrollIntoView({behavior:"smooth", block:"start"});
  };
  el("ed-q").oninput = renderEditor;
  document.querySelectorAll("#ed-pos button").forEach(b => b.onclick = () => {
    document.querySelectorAll("#ed-pos button").forEach(o => o.setAttribute("aria-pressed", o === b ? "true" : "false")); renderEditor(); });
  el("ed-list").onclick = e => { const b = e.target.closest("button[data-add]"); if (!b || b.disabled) return;
    draft.squad.push(+b.dataset.add); renderEditor(); };
  el("ed-squad").onclick = e => { const b = e.target.closest("button[data-rm]"); if (!b) return;
    draft.squad = draft.squad.filter(id => id !== +b.dataset.rm); renderEditor(); };
}
wirePicker();

"""


# ---------------------------------------------------------------------- #
# page
# ---------------------------------------------------------------------- #

def _json(obj) -> str:
    """json.dumps that tolerates numpy scalars and NaN."""
    def default(o):
        try:
            import numpy as _np
            if isinstance(o, _np.integer):
                return int(o)
            if isinstance(o, _np.floating):
                return None if _np.isnan(o) else float(o)
            if isinstance(o, _np.bool_):
                return bool(o)
            if isinstance(o, _np.ndarray):
                return o.tolist()
        except ImportError:
            pass
        return str(o)
    return json.dumps(obj, default=default, allow_nan=False)


def build_dashboard(context: dict) -> str:
    m = context
    xi, bench, bench_gk = m["xi"], m["bench"], m.get("bench_gk")
    captain, vice = m.get("captain"), m.get("vice_captain")
    cap = next((p for p in xi if p["id"] == captain), None)
    vc = next((p for p in xi if p["id"] == vice), None)
    horizon, gw = m["horizon"], m["next_gw"]
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    tiles = [
        ("t-xi", "Projected XI", _fmt(m["xi_points"], 1), "pts",
         f"{m['formation']} · captain included below", False),
        ("t-cap", "Captain", _esc(cap["name"]) if cap else "–", "",
         f"{_fmt(m['captain_ep'], 1)} → {_fmt(m['captain_ep'] * 2, 1)} pts doubled", True),
        ("t-vc", "Vice-captain", _esc(vc["name"]) if vc else "–", "",
         (f"{_fmt(m.get('vice_ep'), 1)} pts · "
          f"{round(100 * float(m.get('vice_reliability', 1)))}% sure to play · "
          "takes the armband if the captain does not feature"), True),
        ("t-val", "Squad value", f"£{_fmt(m['squad_cost'])}m", "",
         f"£{_fmt(m['bank'])}m in the bank", False),
        ("t-hor", f"Next {horizon} GWs", _fmt(m["squad_horizon"], 0), "pts",
         "best XI each week, captain included", False),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="k">{_esc(k)}</div>'
        f'<div class="v{" sm" if small else ""}" id="{tid}">{v}'
        f'{f"<span class=unit> {u}</span>" if u else ""}'
        f'</div><div class="n" id="{tid}-n">{_esc(n)}</div></div>'
        for tid, k, v, u, n, small in tiles)

    source = m.get("strength_source", "")
    notice = ""
    if source and source != "attack/defence split":
        notice = (f'<div class="notice"><b>◔</b><div><b>FPL has not published its '
                  f'full team strength ratings</b> ({_esc(source)}), so the model is '
                  f'rating clubs on FPL\'s fixture difficulty and this season\'s results '
                  f'instead (see Team form). Replayed over a past season this costs '
                  f'about half a point a week — informational, not a fault. It clears '
                  f'itself when FPL publishes the ratings.</div></div>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FPL model · GW{gw}</title>
<style>{CSS}</style></head><body>

<header><div class="hbar">
  <div class="brand">
    <span class="gw">GW{gw}</span>
    <h1>{_esc(m['title'])}</h1>
  </div>
  <span class="count" id="count-down"></span>
  <button class="iconbtn" id="theme" title="Toggle light / dark"
          aria-label="Toggle light or dark theme">◐</button>
</div></header>

<div class="wrap">
  {notice}
  <div class="tiles">{tiles_html}</div>

  <div class="panel" id="squad-panel">
    <div class="squadbar">
      <div class="seg" id="view-seg" role="group" aria-label="Which squad to show">
        <button data-view="mine" aria-pressed="false">My squad</button>
        <button data-view="model" aria-pressed="true">Best £{_budget(m.get('budget', 100.0))} squad</button>
      </div>
      <span class="grow" id="squad-status"></span>
      <button class="btn" id="btn-adopt" hidden title="Open this squad in the editor so you can save it as yours">Make this my squad</button>
      <button class="btn primary" id="btn-pick">Pick my squad</button>
      <button class="btn" id="btn-clear" title="Forget the squad saved in this browser">Clear</button>
    </div>
    <div class="formbar">
      <span class="formbar-k">Formation</span>
      <div class="forms" id="forms" role="group" aria-label="Formation"></div>
    </div>
    <p class="hint" id="form-hint" style="margin-top:-4px"></p>
    <div id="editor" hidden>
      <div class="ed-cols">
        <div>
          <div class="ed-head">
            <input id="ed-q" placeholder="Search player or club" style="min-width:180px;flex:1">
            <div class="seg" id="ed-pos">
              <button data-pos="ALL" aria-pressed="true">All</button>
              <button data-pos="GK" aria-pressed="false">GK</button>
              <button data-pos="DEF" aria-pressed="false">DEF</button>
              <button data-pos="MID" aria-pressed="false">MID</button>
              <button data-pos="FWD" aria-pressed="false">FWD</button>
            </div>
          </div>
          <div class="ed-scroll" id="ed-list"></div>
        </div>
        <div>
          <div class="ed-head"><b>Your fifteen</b> <span class="dim mini">2 GK · 5 DEF · 5 MID · 3 FWD · max 3 per club</span></div>
          <div class="ed-scroll" id="ed-squad"></div>
        </div>
      </div>
      <div class="ed-foot">
        <span id="ed-sum"></span>
        <label>Bank £m <input id="ed-bank" type="number" step="0.1" value="0"></label>
        <label>Free transfers <input id="ed-ft" type="number" step="1" min="0" max="15" value="1"></label>
        <span style="margin-left:auto;display:flex;gap:8px">
          <button class="btn" id="ed-cancel">Cancel</button>
          <button class="btn primary" id="ed-save" disabled>Save squad</button>
        </span>
      </div>
    </div>

    <h2>Starting XI <span class="dim" id="xi-shape" style="font-weight:500"></span></h2>
    <p class="hint">Numbers are projected points for GW{gw}. Deadline
      {_esc(m['deadline'])}.</p>
    <div class="pitch" id="pitch">{_pitch(xi, captain, vice)}</div>

    <h2 style="margin-top:22px">Bench</h2>
    <p class="hint">Set them in this order — first to come on is <b>1</b>.</p>
    <div class="benchrow" id="bench">{_bench(bench, bench_gk)}</div>

    <div class="scale">
      <span>Fixture difficulty</span>
      <span class="chip f1">EASY<i>1</i></span>
      <span class="chip f2">2</span><span class="chip f3">3</span>
      <span class="chip f4">4</span>
      <span class="chip f5">HARD<i>5</i></span>
      <span>· <b>@</b> before a club means away from home</span>
    </div>
  </div>

  <div class="panel">
    <h2>Highest projected points, next {horizon} gameweeks</h2>
    <p class="hint">Every player in the game, not just yours — and deliberately
      <b>not</b> capped at three per club, because this answers "who is worth
      owning". The three-per-club rule applies to the squad above.</p>
    {_bars(m['rankings'])}
  </div>

  {_scorecard(m.get('scorecard'), gw)}

  <div class="panel">
    <h2>Fixture difficulty ticker</h2>
    <p class="hint">Every club's next {horizon} gameweeks, easiest run first.
      <b>Avg</b> is the mean difficulty across the run — the lower it is, the
      better the fixtures. Useful for spotting who to buy into before a good
      patch arrives.</p>
    {_ticker(m.get('ticker', {}))}
  </div>

  {_form(m.get('form'))}

  <div class="panel">
    <h2>Transfer suggestions</h2>
    <p class="hint">Gain is the extra points your <b>starting XI</b> is projected
      to score across the horizon, so upgrading someone who never starts scores
      near zero. Any points hit is already subtracted.</p>
    <div class="tblwrap"><table><thead><tr>
      <th class="num">Moves</th><th>Out</th><th>In</th>
      <th class="num">Bank</th><th class="num">Gain</th><th class="num">Verdict</th>
    </tr></thead><tbody id="transfer-body">{_transfer_rows(m.get('transfers', []))}</tbody></table></div>
  </div>

  <div class="panel">
    <h2>Player rankings</h2>
    <p class="hint"><b>xMins</b> is the minutes the model expects per game;
      <b>Mins</b> is what the player has actually played this season and
      <b>Start</b> how often they start. Set <b>min xMins</b> to around 60 to
      hide rotation risks. Click any column heading to sort.
      <span class="mini" id="count"></span></p>
    <div class="controls">
      <input id="q" placeholder="Search player or club" style="min-width:190px"
             aria-label="Search player or club">
      <div class="seg" role="group" aria-label="Filter by position">
        <button data-pos="ALL" aria-pressed="true">All</button>
        <button data-pos="GK" aria-pressed="false">GK</button>
        <button data-pos="DEF" aria-pressed="false">DEF</button>
        <button data-pos="MID" aria-pressed="false">MID</button>
        <button data-pos="FWD" aria-pressed="false">FWD</button>
      </div>
      <input id="maxp" type="number" step="0.1" value="15" style="width:88px"
             aria-label="Maximum price in millions" title="Maximum price">
      <span class="mini">max £m</span>
      <input id="minmins" type="number" step="5" min="0" max="90" value="0"
             style="width:82px" aria-label="Minimum expected minutes per game"
             title="Hide players expected to play fewer minutes than this">
      <span class="mini">min xMins</span>
    </div>
    <div class="tblwrap"><table><thead><tr>
      <th class="num">#</th>
      <th data-k="name">Player</th><th data-k="pos">Pos</th>
      <th data-k="team_short">Club</th>
      <th class="num" data-k="price">£m</th>
      <th class="num" data-k="ep_next">GW{gw}</th>
      <th class="num" data-k="ep_horizon">Next {horizon}</th>
      <th class="num" data-k="value">Pts/£m</th>
      <th class="num" data-k="xmins">xMins</th>
      <th class="num" data-k="curr_minutes">Mins</th>
      <th class="num" data-k="p_start">Start</th>
      <th class="num" data-k="mean_fdr">FDR</th>
      <th>Fixtures</th>
    </tr></thead><tbody id="rank-body"></tbody></table></div>
  </div>

  <footer>
    Projections blend historical points per 90, current form, expected minutes,
    home or away, and opponent strength. They are estimates, not predictions —
    one gameweek is dominated by luck, and the model has not read a single press
    conference.<br>
    Data: the official Fantasy Premier League API · generated {generated}
  </footer>
</div>
<script>{JS.replace('__RANKS__', _json(m['rankings']))
            .replace('__DEADLINE__', _esc(m.get('deadline_iso', '')))
            .replace('__PLAYERS__', _json(m.get('players', [])))
            .replace('__GWS__', _json(m.get('gws', [])))
            .replace('__INITIAL_SQUAD__', _json(m.get('initial_squad', [])))
            .replace('__MODEL_SQUADS__', _json(m.get('model_squads', {})))
            .replace('__BANK0__', json.dumps(float(m.get('bank_initial', 0) or 0)))
            .replace('__FT0__', json.dumps(int(m.get('free_transfers_initial', 1) or 1)))
            .replace('__FORMATION0__', _esc(m.get('formation', 'auto')))
            .replace('__BUDGET__', json.dumps(float(m.get('budget', 100.0) or 100.0)))}</script>
</body></html>"""
