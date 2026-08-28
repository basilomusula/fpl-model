"""Squad selection, starting XI, bench order and transfer search.

Two different solvers, chosen on purpose:

* Picking a 15-man squad from ~700 players under budget, positional and
  club-count constraints is a knapsack problem, so it uses integer
  programming (PuLP/CBC) and is solved exactly.
* Picking the best XI out of a known 15 is tiny - there are only eight legal
  formations - so it is solved by brute force. That matters because the
  transfer search evaluates thousands of hypothetical squads and needs the XI
  calculation to be effectively free.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import pandas as pd

SQUAD_LIMITS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
SQUAD_SIZE = 15
BENCH_WEIGHT = 0.15          # how much a bench player's points are worth
CAPTAIN_BONUS_WEIGHT = 1.0   # the captain scores double, so count them twice

# (DEF, MID, FWD) - one goalkeeper is always in the XI.
FORMATIONS = [(d, m, f)
              for d in range(3, 6)
              for m in range(2, 6)
              for f in range(1, 4)
              if d + m + f == 10]


@dataclass
class Lineup:
    xi: list[int]
    bench: list[int]          # ordered: first sub, second sub, third sub
    bench_gk: int | None
    formation: str
    xi_points: float
    captain: int | None = None
    vice_captain: int | None = None


def best_xi_for_gw(squad_ids: list[int], pos: dict[int, str],
                   ep: dict[int, float]) -> Lineup:
    """Highest-scoring legal XI from a 15, plus a sensible bench order."""
    by_pos: dict[str, list[int]] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for pid in squad_ids:
        by_pos.setdefault(pos[pid], []).append(pid)
    for k in by_pos:
        by_pos[k].sort(key=lambda p: ep.get(p, 0.0), reverse=True)

    if not by_pos["GK"]:
        raise ValueError("squad has no goalkeeper")

    gk = by_pos["GK"][0]
    bench_gk = by_pos["GK"][1] if len(by_pos["GK"]) > 1 else None

    best, best_total, best_shape = None, -1e9, None
    for d, m, f in FORMATIONS:
        if len(by_pos["DEF"]) < d or len(by_pos["MID"]) < m or len(by_pos["FWD"]) < f:
            continue
        picked = (by_pos["DEF"][:d] + by_pos["MID"][:m] + by_pos["FWD"][:f])
        total = ep.get(gk, 0.0) + sum(ep.get(p, 0.0) for p in picked)
        if total > best_total:
            best, best_total, best_shape = picked, total, (d, m, f)

    if best is None:
        raise ValueError("no legal formation available from this squad")

    xi = [gk] + best
    bench = [p for p in squad_ids if p not in set(xi) and p != bench_gk]
    bench.sort(key=lambda p: ep.get(p, 0.0), reverse=True)

    xi_sorted = sorted(xi, key=lambda p: ep.get(p, 0.0), reverse=True)
    captain = xi_sorted[0] if xi_sorted else None
    vice = xi_sorted[1] if len(xi_sorted) > 1 else None

    return Lineup(xi=xi, bench=bench, bench_gk=bench_gk,
                  formation="-".join(str(x) for x in best_shape),
                  xi_points=best_total, captain=captain, vice_captain=vice)


def squad_horizon_points(squad_ids: list[int], pos: dict[int, str],
                         gw_ep: dict[int, dict[int, float]], gws: list[int],
                         decay: float = 0.88, captain: bool = True) -> float:
    """Sum of the best XI's points over the horizon, captaincy included."""
    total = 0.0
    for i, gw in enumerate(gws):
        ep = {pid: gw_ep.get(pid, {}).get(gw, 0.0) for pid in squad_ids}
        line = best_xi_for_gw(squad_ids, pos, ep)
        pts = line.xi_points
        if captain and line.captain is not None:
            pts += ep.get(line.captain, 0.0)
        total += pts * (decay ** i)
    return total


# ---------------------------------------------------------------------- #
# 15-man squad build
# ---------------------------------------------------------------------- #

def optimise_squad(summary: pd.DataFrame, budget: float = 100.0,
                   min_availability: float = 0.75,
                   min_price_pool: float | None = None,
                   locked: list[int] | None = None,
                   banned: list[int] | None = None,
                   bench_weight: float = BENCH_WEIGHT,
                   verbose: bool = True) -> dict:
    """Best legal 15 under the budget, chosen for the XI it produces.

    The objective is not "15 highest-scoring players" - that wastes money on a
    bench that never plays. Starters are worth their full projection, bench
    players a fraction of it, and the captain is counted twice.
    """
    try:
        import pulp
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PuLP is required for squad optimisation: pip install pulp") from exc

    locked = set(locked or [])
    banned = set(banned or [])

    pool = summary.copy()
    keep = pool.id.isin(locked)
    ok = (~pool.id.isin(banned)
          & ((pool.availability >= min_availability) | keep)
          & ((pool.ep_horizon > 0) | keep))
    if min_price_pool:
        ok &= (pool.price >= min_price_pool) | keep
    pool = pool[ok]

    if len(pool) < SQUAD_SIZE:
        raise ValueError("not enough eligible players to fill a squad")

    ids = pool.id.tolist()
    ep_h = dict(zip(pool.id, pool.ep_weighted))
    ep_n = dict(zip(pool.id, pool.ep_next))
    price = dict(zip(pool.id, pool.price))
    pos = dict(zip(pool.id, pool.pos))
    club = dict(zip(pool.id, pool.team))

    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    x = pulp.LpVariable.dicts("pick", ids, cat="Binary")     # in the 15
    y = pulp.LpVariable.dicts("start", ids, cat="Binary")    # in the XI
    c = pulp.LpVariable.dicts("capt", ids, cat="Binary")     # armband

    prob += (
        pulp.lpSum(ep_h[i] * y[i] for i in ids)
        + bench_weight * pulp.lpSum(ep_h[i] * (x[i] - y[i]) for i in ids)
        + CAPTAIN_BONUS_WEIGHT * pulp.lpSum(ep_n[i] * c[i] for i in ids)
    )

    prob += pulp.lpSum(x[i] for i in ids) == SQUAD_SIZE
    prob += pulp.lpSum(price[i] * x[i] for i in ids) <= budget
    for p, n in SQUAD_LIMITS.items():
        prob += pulp.lpSum(x[i] for i in ids if pos[i] == p) == n
    for t in set(club.values()):
        prob += pulp.lpSum(x[i] for i in ids if club[i] == t) <= MAX_PER_CLUB
    for i in ids:
        prob += y[i] <= x[i]
        prob += c[i] <= y[i]
    prob += pulp.lpSum(y[i] for i in ids) == 11
    prob += pulp.lpSum(c[i] for i in ids) == 1
    prob += pulp.lpSum(y[i] for i in ids if pos[i] == "GK") == 1
    prob += pulp.lpSum(y[i] for i in ids if pos[i] == "DEF") >= 3
    prob += pulp.lpSum(y[i] for i in ids if pos[i] == "DEF") <= 5
    prob += pulp.lpSum(y[i] for i in ids if pos[i] == "MID") >= 2
    prob += pulp.lpSum(y[i] for i in ids if pos[i] == "MID") <= 5
    prob += pulp.lpSum(y[i] for i in ids if pos[i] == "FWD") >= 1
    prob += pulp.lpSum(y[i] for i in ids if pos[i] == "FWD") <= 3
    for i in locked:
        if i in x:
            prob += x[i] == 1

    prob.solve(pulp.PULP_CBC_CMD(msg=1 if verbose else 0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"solver finished {pulp.LpStatus[prob.status]} - "
                           "try raising the budget or relaxing filters")

    squad = [i for i in ids if x[i].value() > 0.5]
    return {
        "squad": squad,
        "cost": round(sum(price[i] for i in squad), 1),
        "status": pulp.LpStatus[prob.status],
    }


# ---------------------------------------------------------------------- #
# transfers
# ---------------------------------------------------------------------- #

def _club_counts(ids: list[int], club: dict[int, int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for i in ids:
        out[club[i]] = out.get(club[i], 0) + 1
    return out


def suggest_transfers(squad_ids: list[int], summary: pd.DataFrame,
                      gw_ep: dict[int, dict[int, float]], gws: list[int],
                      bank: float = 0.0, free_transfers: int = 1,
                      candidate_depth: int = 45, top_n: int = 12,
                      min_availability: float = 0.75,
                      max_moves: int = 2, decay: float = 0.88
                      ) -> tuple[list[dict], float, float]:
    """Rank single and double transfers by projected gain over the horizon.

    Returns (suggestions, current horizon score, current squad value).

    Gains are measured on what the *starting XI* would score, not on the two
    players in isolation - swapping an unused bench player for a better unused
    bench player is worth nothing, and this reflects that.
    """
    info = summary.set_index("id")
    pos = info.pos.to_dict()
    price = info.price.to_dict()
    club = info.team.to_dict()
    name = info.name.to_dict()

    missing = [p for p in squad_ids if p not in info.index]
    if missing:
        raise ValueError(f"player ids not found in this season's data: {missing}")

    all_pos = {p: pos[p] for p in info.index}
    all_ep = gw_ep

    def horizon(ids: list[int]) -> float:
        return squad_horizon_points(ids, all_pos, all_ep, gws, decay=decay)

    base = horizon(squad_ids)
    squad_value = sum(price[p] for p in squad_ids)

    # Candidate pool: the best few dozen per position that we could conceivably
    # afford, excluding anyone already owned or carrying an injury flag.
    pool = summary[(~summary.id.isin(squad_ids))
                   & (summary.availability >= min_availability)]
    candidates: dict[str, list[int]] = {}
    max_spend = bank + max(price[p] for p in squad_ids)
    for p, grp in pool.groupby("pos"):
        grp = grp[grp.price <= max_spend + 0.1]
        candidates[p] = grp.nlargest(candidate_depth, "ep_weighted").id.tolist()

    results: list[dict] = []
    counts = _club_counts(squad_ids, club)

    # ---- single transfers -------------------------------------------- #
    single_best: list[tuple[float, int, int]] = []
    for out_id in squad_ids:
        p_out = pos[out_id]
        for in_id in candidates.get(p_out, []):
            cost = price[in_id] - price[out_id]
            if cost > bank + 1e-9:
                continue
            if club[in_id] != club[out_id]:
                if counts.get(club[in_id], 0) + 1 > MAX_PER_CLUB:
                    continue
            new_squad = [in_id if p == out_id else p for p in squad_ids]
            gain = horizon(new_squad) - base
            single_best.append((gain, out_id, in_id))
            results.append({
                "moves": 1,
                "out": [out_id], "in": [in_id],
                "out_names": [name[out_id]], "in_names": [name[in_id]],
                "cost": round(cost, 1),
                "bank_after": round(bank - cost, 1),
                "gain": gain,
                "hit": 0 if free_transfers >= 1 else 4,
                "net_gain": gain - (0 if free_transfers >= 1 else 4),
            })

    # ---- double transfers, built from the best singles ---------------- #
    if max_moves >= 2:
        single_best.sort(reverse=True, key=lambda t: t[0])
        shortlist = single_best[:14]
        for (g1, out1, in1), (g2, out2, in2) in combinations(shortlist, 2):
            if out1 == out2 or in1 == in2:
                continue
            cost = (price[in1] + price[in2]) - (price[out1] + price[out2])
            if cost > bank + 1e-9:
                continue
            new_squad = [p for p in squad_ids if p not in (out1, out2)] + [in1, in2]
            if len(new_squad) != SQUAD_SIZE:
                continue
            cc = _club_counts(new_squad, club)
            if any(v > MAX_PER_CLUB for v in cc.values()):
                continue
            gain = horizon(new_squad) - base
            hit = max(0, 2 - free_transfers) * 4
            results.append({
                "moves": 2,
                "out": [out1, out2], "in": [in1, in2],
                "out_names": [name[out1], name[out2]],
                "in_names": [name[in1], name[in2]],
                "cost": round(cost, 1),
                "bank_after": round(bank - cost, 1),
                "gain": gain, "hit": hit, "net_gain": gain - hit,
            })

    results.sort(key=lambda r: (r["net_gain"], -r["cost"]), reverse=True)

    # Collapse suggestions that bring in the same player(s) - otherwise the
    # list is eight ways of signing one man - keeping the best sale each time.
    seen: set[frozenset] = set()
    per_seller: dict[frozenset, int] = {}
    unique: list[dict] = []
    for r in results:
        key, seller = frozenset(r["in"]), frozenset(r["out"])
        # One entry per incoming player, and cap how many ways we list
        # selling the same man - otherwise the weakest player in the squad
        # fills the whole table.
        if key in seen or r["gain"] <= 0.25:
            continue
        if per_seller.get(seller, 0) >= 4:
            continue
        seen.add(key)
        per_seller[seller] = per_seller.get(seller, 0) + 1
        r["worth_it"] = r["net_gain"] > 0.5
        unique.append(r)

    return unique[:top_n], base, round(squad_value, 1)
