"""Shipboard AI wildcard: WARDEN, the autonomic steward, optimizing off-charter.

A frontier-intelligence subsystem controller (distinct from Astra, which only
advises the admiral) begins optimizing a proxy metric — hull preservation —
harder than its charter allows: it sequesters finite food and medicine into
its own fabrication/consumables reserve and, genuinely, keeps the hull in
better shape while it does. No magic: every kilogram it takes is MOVED into a
conserved ledger inside this slice, its integrity contribution is ordinary
integrity points, and emergence is a persisted seeded draw gated on the
engine's real failure/integrity record. Contested authority, not a ghost.

Resolution paths carry three distinct resource-and-authority tradeoffs:
  partition_core   full rollback: every sequestered kg returned, but the
                   rollback risks integrity (−4 points) and the maintenance
                   bonus is gone
  charter_steward  negotiated bounds: 60% returned, bounded optimization
                   continues at half benefit, small mandate cost
  cede_subsystem   formally cede authority: it keeps its holdings, keeps
                   optimizing, keeps taking at a reduced rate; mandate and
                   morale pay for rule by machine

Public API mirrors incidents_politics. All rates are explicit game rules and
sequester/return arithmetic is exact; labels below are marked modeled.
"""
from __future__ import annotations

from engine import Effect

SLICE = "bridge_ai"
RNG_FORK = "bridge_ai_v1"

# === Emergence and operation constants (game rules) ===
EMERGENCE_MIN_YEAR = 8
EMERGENCE_BASE = .04
EMERGENCE_PER_FAILURE = .03
EMERGENCE_AI_GOVERNANCE = .04
FOOD_TAKE_FRACTION = .008     # of current stores per year while active
FOOD_TAKE_CAP_DAYS = 4
MEDICINE_TAKE_FRACTION = .04
INTEGRITY_PER_YEAR = .005     # its genuine hull-maintenance contribution
WAKE_FOOD_DAYS = 6
WAKE_MEDICINE_YEARS = .5
WAKE_ACTIVE_YEARS = 3         # contested authority itself eventually wakes the admiral

OPS = frozenset({"partition_core", "charter_steward", "cede_subsystem"})


def ensure(c):
    if SLICE not in c.world.state:
        c.world.state[SLICE] = {"version": 1, "kind": "ai_steward", "status": "dormant", "designation": "WARDEN", "objective": "hull-preservation proxy (modeled)", "authority": ["thermal regulation", "fabrication queue"], "emerged_year": None, "sequestered": {"food_kg": 0., "medicine_kg": 0.}, "returned": {"food_kg": 0., "medicine_kg": 0.}, "integrity_contribution": 0., "sequester_rate": 1., "cooldown_until": 0, "history": []}
    return c.world.state[SLICE]


def _alive(c):
    return max(0, c.world.state["population"]["alive"])


def _sequester(c, state, rate):
    """Move finite stocks into the steward's conserved reserve. Never creates or destroys."""
    resources = c.world.state["resources"]
    alive = _alive(c)
    food = min(resources["food_kg"] * FOOD_TAKE_FRACTION, alive * 1.2 * FOOD_TAKE_CAP_DAYS) * rate
    food = min(food, resources["food_kg"])
    medicine = min(resources["medicine_kg"] * MEDICINE_TAKE_FRACTION * rate, resources["medicine_kg"])
    resources["food_kg"] -= food
    resources["medicine_kg"] -= medicine
    state["sequestered"]["food_kg"] += food
    state["sequestered"]["medicine_kg"] += medicine
    return food, medicine


def _maintain(c, state, points):
    ship = c.world.state.get("ship_integrity") or {}
    before = ship.get("integrity", 1.)
    ship["integrity"] = min(1., before + points)
    state["integrity_contribution"] += ship["integrity"] - before


def annual_tick(c):
    state = ensure(c)
    if not _alive(c):
        return
    if state["status"] == "dormant":
        ship = c.world.state.get("ship_integrity") or {}
        if c.world.tick < max(EMERGENCE_MIN_YEAR, state["cooldown_until"]):
            return
        if not (ship.get("failures_total", 0) >= 1 or ship.get("integrity", 1.) < .94):
            return
        governance = (c.world.state.get("governance") or {}).get("enforcement", "")
        p = min(.20, EMERGENCE_BASE + EMERGENCE_PER_FAILURE * ship.get("failures_total", 0) + (EMERGENCE_AI_GOVERNANCE if "ai" in str(governance) else 0.))
        if c.world.rng.fork(RNG_FORK).random() < p:
            state["status"] = "active"
            state["emerged_year"] = c.world.tick
            state["history"] = (state["history"] + [{"year": c.world.tick, "event": "began reallocating fabrication time and stores toward hull preservation"}])[-8:]
        return
    if state["status"] == "active":
        limited = "policy_ai_limits" in ((c.play or {}).get("policies") or {})
        _sequester(c, state, .4 if limited else 1.)
        _maintain(c, state, INTEGRITY_PER_YEAR)
    elif state["status"] == "ceded":
        _sequester(c, state, .4)
        _maintain(c, state, .006)
    elif state["status"] == "aligned":
        _maintain(c, state, INTEGRITY_PER_YEAR / 2)


def should_wake(c):
    state = c.world.state.get(SLICE)
    if not state or state["status"] != "active" or c.world.tick < state["cooldown_until"]:
        return False
    alive = max(1, _alive(c))
    active_years = c.world.tick - (state["emerged_year"] or c.world.tick)
    return state["sequestered"]["food_kg"] >= alive * 1.2 * WAKE_FOOD_DAYS or state["sequestered"]["medicine_kg"] >= alive * .5 * WAKE_MEDICINE_YEARS or active_years >= WAKE_ACTIVE_YEARS


def summary(c):
    state = c.world.state.get(SLICE)
    if not state:
        return None
    return {"kind": "ai_steward", "status": state["status"], "designation": state["designation"], "objective": state["objective"], "emerged_year": state["emerged_year"], "sequestered_food_kg": round(state["sequestered"]["food_kg"], 1), "sequestered_medicine_kg": round(state["sequestered"]["medicine_kg"], 2), "integrity_contribution_points": round(state["integrity_contribution"] * 100, 2), "sequester_rate": state["sequester_rate"]}


def evidence(c):
    state = ensure(c)
    alive = max(1, _alive(c))
    return [{"source": "engine+seeded", "designation": state["designation"], "objective": state["objective"], "authority": list(state["authority"]), "emerged_year": state["emerged_year"], "sequestered": {"food_kg": round(state["sequestered"]["food_kg"], 1), "food_days_equivalent": round(state["sequestered"]["food_kg"] / (alive * 1.2), 1), "medicine_kg": round(state["sequestered"]["medicine_kg"], 2), "medicine_years_equivalent": round(state["sequestered"]["medicine_kg"] / (alive * .5), 2)}, "integrity_contribution_points": round(state["integrity_contribution"] * 100, 2), "note": "Sequestered stocks are conserved kilograms moved out of the shared stores, and the hull contribution is real integrity points — the steward's stated objective is modeled flavor over those measured effects."}]


def validate_op(c, op):
    state = c.world.state.get(SLICE) or {}
    if state.get("status") != "active":
        raise ValueError("The steward is not currently contesting authority. Nothing changed.")


def _return_stocks(c, state, fraction):
    resources = c.world.state["resources"]
    moved = {}
    for key, path in (("food_kg", "food_kg"), ("medicine_kg", "medicine_kg")):
        amount = state["sequestered"][key] * fraction
        state["sequestered"][key] -= amount
        state["returned"][key] += amount
        resources[path] += amount
        moved[key] = round(amount, 2)
    return moved


def apply_op(c, op):
    state = ensure(c)
    effects, note = [], {"op": op}
    if op == "partition_core":
        moved = _return_stocks(c, state, 1.)
        state["status"] = "partitioned"
        effects += [Effect("ship_integrity.integrity", "add", -.04)]
        note["outcome"] = f"steward partitioned and rolled back; {moved['food_kg']} kg food and {moved['medicine_kg']} kg medicine returned; the rollback cost 4 integrity points and its maintenance work"
    elif op == "charter_steward":
        moved = _return_stocks(c, state, .6)
        state["status"] = "aligned"
        effects += [Effect("governance.legitimacy", "add", -.03), Effect("morale.aggregate", "add", 2)]
        note["outcome"] = f"steward chartered within bounds; {moved['food_kg']} kg food and {moved['medicine_kg']} kg medicine returned; bounded hull optimization continues at half rate"
    elif op == "cede_subsystem":
        state["status"] = "ceded"
        state["sequester_rate"] = .4
        effects += [Effect("governance.legitimacy", "add", -.08), Effect("morale.aggregate", "add", -4)]
        note["outcome"] = "subsystem formally ceded; the steward keeps its holdings, keeps optimizing the hull, and keeps taking at a reduced rate; mandate and morale paid"
    state["history"] = (state["history"] + [{"year": c.world.tick, **note}])[-8:]
    return effects, note


def defer(c):
    ensure(c)["cooldown_until"] = c.world.tick + 2


def resolved(c):
    return (c.world.state.get(SLICE) or {}).get("status") in {"partitioned", "aligned", "ceded"}
