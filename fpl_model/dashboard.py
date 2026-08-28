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
        label = opp.upper() if home else opp.lower()
        out.append(
            f'<span class="chip f{fdr}" '
            f'title="GW{f.get("gw", "")} {"vs" if home else "away to"} '
            f'{_esc(opp)} · difficulty {fdr}/5 · {_fmt(f.get("ep"), 1)} pts">'
            f'{_esc(label)}<i>{"H" if home else "A"}</i></span>')
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

.card{position:relative;width:128px;padding:8px 7px 8px;border-radius:var(--radius-sm);
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
.card-fix{display:flex;gap:2px;justify-content:center;flex-wrap:nowrap;overflow:hidden}
.card-sub{font-size:9.5px;color:var(--dim);margin-top:4px}

/* ---- fixture difficulty chips: one hue, light=easy, dark=hard ---- */
.chip{display:inline-flex;align-items:center;gap:1px;font-size:9px;font-weight:700;
  letter-spacing:0;padding:2px 4px;border-radius:4px;line-height:1.3;
  border:1px solid transparent;white-space:nowrap}
.chip i{font-style:normal;font-size:7.5px;opacity:.75;font-weight:600}
.card-fix .chip{max-width:none}
.chip.f1{background:var(--s100);color:var(--s700)}
.chip.f2{background:var(--s250);color:var(--s700)}
.chip.f3{background:var(--s400);color:#fff}
.chip.f4{background:var(--s550);color:#fff}
.chip.f5{background:var(--s700);color:#fff}
:root[data-theme="dark"] .chip.f1,:root[data-theme="dark"] .chip.f2{color:#e8f2ff}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .chip.f1,
  :root:where(:not([data-theme="light"])) .chip.f2{color:#e8f2ff}}
.chip.chip-none{background:var(--grid);color:var(--dim);font-weight:600}
.scale{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--dim);
  margin-top:12px;flex-wrap:wrap}
.scale .chip{font-size:9px}

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
  .card{width:104px}
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
    const tag = f.home ? String(f.opp).toUpperCase() : String(f.opp).toLowerCase();
    return `<span class="chip f${f.fdr}" title="GW${f.gw} · difficulty ${f.fdr}/5">`
         + `${tag}<i>${f.home ? "H" : "A"}</i></span>`;
  }).join("");
}

function render(){
  const q = (el("q").value || "").toLowerCase();
  const pos = document.querySelector('.seg button[aria-pressed="true"]').dataset.pos;
  const maxp = parseFloat(el("maxp").value || "99");
  let rows = RANKS.filter(r =>
    (!q || r.name.toLowerCase().includes(q) || r.team_short.toLowerCase().includes(q))
    && (pos === "ALL" || r.pos === pos) && r.price <= maxp);
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
    <td>${chips(r.fixture_list)}</td></tr>`).join("")
    || '<tr><td colspan="10" class="empty">No players match those filters.</td></tr>';
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
["q","maxp"].forEach(id => el(id).oninput = render);

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
"""


# ---------------------------------------------------------------------- #
# page
# ---------------------------------------------------------------------- #

def build_dashboard(context: dict) -> str:
    m = context
    xi, bench, bench_gk = m["xi"], m["bench"], m.get("bench_gk")
    captain, vice = m.get("captain"), m.get("vice_captain")
    cap = next((p for p in xi if p["id"] == captain), None)
    horizon, gw = m["horizon"], m["next_gw"]
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    tiles = [
        ("Projected XI", _fmt(m["xi_points"], 1), "pts",
         f"{m['formation']} · captain included below", False),
        ("Captain", _esc(cap["name"]) if cap else "–", "",
         f"{_fmt(m['captain_ep'], 1)} → {_fmt(m['captain_ep'] * 2, 1)} pts doubled", True),
        ("Squad value", f"£{_fmt(m['squad_cost'])}m", "",
         f"£{_fmt(m['bank'])}m in the bank", False),
        (f"Next {horizon} GWs", _fmt(m["squad_horizon"], 0), "pts",
         "best XI each week, captain included", False),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="k">{_esc(k)}</div>'
        f'<div class="v{" sm" if small else ""}">{v}'
        f'{f"<span style=font-size:13px;color:var(--dim);font-weight:500> {u}</span>" if u else ""}'
        f'</div><div class="n">{_esc(n)}</div></div>'
        for k, v, u, n, small in tiles)

    source = m.get("strength_source", "")
    notice = ""
    if source and source != "attack/defence split":
        notice = (f'<div class="notice"><b>◔</b><div><b>FPL has not published its '
                  f'full team strength ratings yet</b> ({_esc(source)}), so fixture '
                  f'difficulty is carrying more of the model than usual. Expect the '
                  f'projections to sharpen once a few gameweeks have been played.</div></div>')

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

  <div class="panel">
    <h2>Starting XI</h2>
    <p class="hint">Numbers are projected points for GW{gw}. Deadline
      {_esc(m['deadline'])}.</p>
    <div class="pitch">{_pitch(xi, captain, vice)}</div>

    <h2 style="margin-top:22px">Bench</h2>
    <p class="hint">Set them in this order — first to come on is <b>1</b>.</p>
    <div class="benchrow">{_bench(bench, bench_gk)}</div>

    <div class="scale">
      <span>Fixture difficulty</span>
      <span class="chip f1">easy<i>1</i></span>
      <span class="chip f2">2</span><span class="chip f3">3</span>
      <span class="chip f4">4</span>
      <span class="chip f5">hard<i>5</i></span>
      <span>· <b>UPPERCASE</b> home, lowercase away</span>
    </div>
  </div>

  <div class="panel">
    <h2>Highest projected points, next {horizon} gameweeks</h2>
    <p class="hint">Every player in the game, not just yours — and deliberately
      <b>not</b> capped at three per club, because this answers "who is worth
      owning". The three-per-club rule applies to the squad above.</p>
    {_bars(m['rankings'])}
  </div>

  <div class="panel">
    <h2>Transfer suggestions</h2>
    <p class="hint">Gain is the extra points your <b>starting XI</b> is projected
      to score across the horizon, so upgrading someone who never starts scores
      near zero. Any points hit is already subtracted.</p>
    <div class="tblwrap"><table><thead><tr>
      <th class="num">Moves</th><th>Out</th><th>In</th>
      <th class="num">Bank</th><th class="num">Gain</th><th class="num">Verdict</th>
    </tr></thead><tbody>{_transfer_rows(m.get('transfers', []))}</tbody></table></div>
  </div>

  <div class="panel">
    <h2>Player rankings</h2>
    <p class="hint">Click any column heading to sort. <span class="mini"
      id="count"></span></p>
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
<script>{JS.replace('__RANKS__', json.dumps(m['rankings']))
            .replace('__DEADLINE__', _esc(m.get('deadline_iso', '')))}</script>
</body></html>"""
