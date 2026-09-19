"""Organized-influence network: theft of finite stores, coercion, corruption.

The network is staffed from the ACTUAL roster — ringleaders and corrupted
officials are living crew ids drawn with the campaign's persisted RNG fork, so
saves reproduce exactly and a purge names real people with real ages and
histories. Every stolen kilogram is conserved in an explicit ledger:

    skimmed = consumed (unrecoverable, spent/traded inside the network)
            + held    (cached aboard; a purge recovers it)
            + recovered (already returned to stores)

Resolution paths:
  purge_network  dissolves it and recovers the caches, but the sweep costs
                 morale and mandate (a crackdown people feel)
  turn_network   informants; goes dormant (can return), exposed corruption
                 restores some mandate, no stores recovered
  ration_deal    buy them out with food; fast and bloodless, mandate pays

Public API mirrors incidents_politics. Explicit game rules, not criminology.
"""
from __future__ import annotations

from engine import Effect

SLICE = "bridge_crime"
RNG_FORK = "bridge_crime_v1"

# === Formation / operation constants (game rules) ===
FORMATION_BASE = .03
FORMATION_MIN_YEAR = 5
SKIM_FRACTION = .015          # of current food stores per year
SKIM_CAP_DAYS = 8             # never more than this many crew-days per year
HELD_SHARE = .40              # cached and recoverable; the rest is consumed
CORRUPTION_INTERVAL = 2       # years between newly corrupted officials
WATCH_FACTOR = .35            # policy_watch multiplier on formation/reactivation
WAKE_SKIM_DAYS = 10           # wake once theft reaches this many crew-days
WAKE_CORRUPTED = 2

OPS = frozenset({"purge_network", "turn_network", "ration_deal"})


def ensure(c):
    if SLICE not in c.world.state:
        c.world.state[SLICE] = {"version": 1, "kind": "crime", "status": "none", "network": None, "skimmed_food_kg": 0., "consumed_food_kg": 0., "recovered_food_kg": 0., "corrupted": [], "coercion_reports": 0, "cooldown_until": 0, "networks_total": 0, "history": []}
    return c.world.state[SLICE]


def _held_kg(state):
    return max(0., state["skimmed_food_kg"] - state["consumed_food_kg"] - state["recovered_food_kg"])


def _adults(c, minimum_age=21):
    return sorted((str(pid) for pid, p in c.world.state["population"]["people"].items() if p["alive"] and p["age"] >= minimum_age), key=int)


def _watch_active(c):
    return "policy_watch" in ((c.play or {}).get("policies") or {})


def _formation_pressure(c):
    """Deterministic annual probability from actual deprivation and alienation."""
    s = c.summary()
    factions = (c.world.state.get("factions") or {}).get("factions") or {}
    alienation = max((f["alienation"] for f in factions.values()), default=.1)
    p = FORMATION_BASE
    p += .05 * (s["morale"] < 50) + .03 * (s["morale"] < 40)
    p += .03 * (s["food_days"] < 160) + .03 * (s["food_adequacy"] < .95)
    p += .10 * max(0., alienation - .40)
    p += .02 * (s["mandate"] < 50)
    return min(.3, p) * (WATCH_FACTOR if _watch_active(c) else 1.)


def annual_tick(c):
    state = ensure(c)
    rng = c.world.rng.fork(RNG_FORK)
    tick = c.world.tick
    people = c.world.state["population"]["people"]
    alive = sum(bool(p["alive"]) for p in people.values())
    if not alive:
        return
    if state["status"] == "none" and tick >= max(FORMATION_MIN_YEAR, state["cooldown_until"]):
        if rng.random() < _formation_pressure(c):
            adults = _adults(c)
            factions = (c.world.state.get("factions") or {}).get("factions") or {}
            root = max(factions.items(), key=lambda kv: kv[1]["alienation"], default=(None, None))
            rng.shuffle(adults)
            leaders = adults[:3]
            state["status"] = "active"
            state["networks_total"] += 1
            state["network"] = {"formed_year": tick, "root_faction": root[0], "root_faction_name": root[1]["name"] if root[1] else None, "ringleaders": leaders, "members": max(4, round(alive * .02)), "years": 0}
        return
    if state["status"] == "dormant":
        chance = .12 * (WATCH_FACTOR if _watch_active(c) else 1.)
        if tick >= state["cooldown_until"] and rng.random() < chance:
            state["status"] = "active"
            state["network"]["years"] = 0
        return
    if state["status"] != "active":
        return
    net = state["network"]
    net["years"] += 1
    watch = _watch_active(c)
    # Theft of finite stores: skimmed food leaves resources the moment it is taken.
    resources = c.world.state["resources"]
    skim = min(resources["food_kg"] * SKIM_FRACTION, alive * 1.2 * SKIM_CAP_DAYS) * (.5 if watch else 1.)
    skim = min(skim, resources["food_kg"])
    resources["food_kg"] -= skim
    state["skimmed_food_kg"] += skim
    state["consumed_food_kg"] += skim * (1 - HELD_SHARE)
    if not watch:
        net["members"] = round(net["members"] * 1.08) + 1
    # Corruption of actual officials: real living adults, drawn seeded.
    if net["years"] % CORRUPTION_INTERVAL == 0:
        pool = [pid for pid in _adults(c, 30) if pid not in state["corrupted"] and pid not in net["ringleaders"]]
        if pool:
            state["corrupted"].append(rng.choice(pool))
            gov = c.world.state.get("governance") or {}
            gov["legitimacy"] = max(0., gov.get("legitimacy", .85) - .015)
    # Coercion is felt as morale, reported as counts, never as deaths.
    state["coercion_reports"] += rng.randint(0, 2)
    morale = c.world.state.get("morale", {})
    morale["aggregate"] = max(0., morale.get("aggregate", 0.) - .5)


def should_wake(c):
    state = c.world.state.get(SLICE)
    if not state or state["status"] != "active" or c.world.tick < state["cooldown_until"]:
        return False
    alive = max(1, c.world.state["population"]["alive"])
    return _held_kg(state) + state["consumed_food_kg"] >= alive * 1.2 * WAKE_SKIM_DAYS or len(state["corrupted"]) >= WAKE_CORRUPTED


def summary(c):
    state = c.world.state.get(SLICE)
    if not state:
        return None
    return {"kind": "crime", "status": state["status"], "network": dict(state["network"]) if state["network"] else None, "skimmed_food_kg": round(state["skimmed_food_kg"], 1), "held_food_kg": round(_held_kg(state), 1), "recovered_food_kg": round(state["recovered_food_kg"], 1), "corrupted_officials": len(state["corrupted"]), "coercion_reports": state["coercion_reports"], "networks_total": state["networks_total"]}


def evidence(c):
    state = ensure(c)
    rows = {r["id"]: r for r in c.crew()}
    def named(pids):
        return [{"id": pid, "name": rows[pid]["name"], "age": rows[pid]["age"], "specialty": rows[pid]["specialty"], "generation": rows[pid]["generation"]} for pid in pids if pid in rows]
    net = state["network"] or {}
    return [{"source": "engine+seeded-roster", "network": {k: net.get(k) for k in ("formed_year", "root_faction_name", "members", "years")}, "ringleaders": named(net.get("ringleaders", [])), "corrupted_officials": named(state["corrupted"]), "stores": {"skimmed_food_kg": round(state["skimmed_food_kg"], 1), "held_in_caches_kg": round(_held_kg(state), 1), "consumed_kg": round(state["consumed_food_kg"], 1)}, "coercion_reports": state["coercion_reports"], "note": "Named people are the actual roster (fictional dressing over simulated ages/histories); the food ledger is conserved kilogram for kilogram."}]


def validate_op(c, op):
    state = c.world.state.get(SLICE) or {}
    if state.get("status") != "active":
        raise ValueError("There is no active network to move against. Nothing changed.")


def apply_op(c, op):
    state = ensure(c)
    net = state["network"]
    effects, note = [], {"op": op, "members": net["members"] if net else 0}
    if op == "purge_network":
        recovered = _held_kg(state)
        c.world.state["resources"]["food_kg"] += recovered
        state["recovered_food_kg"] += recovered
        state["status"] = "dissolved"
        state["cooldown_until"] = c.world.tick + 8
        state["corrupted"] = []
        effects += [Effect("morale.aggregate", "add", -7), Effect("governance.legitimacy", "add", -.06)]
        note["outcome"] = f"network dissolved; {round(recovered)} kg of cached stores recovered; the sweep cost morale and mandate"
    elif op == "turn_network":
        state["status"] = "dormant"
        state["cooldown_until"] = c.world.tick + 5
        exposed = len(state["corrupted"])
        state["corrupted"] = []
        effects += [Effect("governance.legitimacy", "add", .04), Effect("morale.aggregate", "add", 2)]
        note["outcome"] = f"network dormant under informants; {exposed} corrupted official(s) exposed; caches were not found"
    elif op == "ration_deal":
        # The 5% food payment is charged by the standard food_cost seam.
        state["status"] = "dissolved"
        state["cooldown_until"] = c.world.tick + 8
        effects += [Effect("governance.legitimacy", "add", -.05), Effect("morale.aggregate", "add", 3)]
        note["outcome"] = "network bought out and dissolved; paying it off cost mandate; corruption stays unexposed"
    state["history"] = (state["history"] + [{"year": c.world.tick, **note}])[-8:]
    return effects, note


def defer(c):
    ensure(c)["cooldown_until"] = c.world.tick + 3


def resolved(c):
    return (c.world.state.get(SLICE) or {}).get("status") in {"dissolved", "dormant"}
