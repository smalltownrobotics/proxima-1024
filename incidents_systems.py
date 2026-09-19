"""Engineering cascade: the failures the engine already models, chaining.

The engine's ship_integrity system produces real failure kinds (engine_failure,
hull_breach, life_support_failure, ...) with severities and recovery clocks.
This module adds no new physics — it tracks STRAIN, an explicit bridge-side
measure of how far repairs are falling behind those actual failures, and turns
a chain of them into a locked incident with harder choices than one patch.

Resolution paths:
  overhaul           the expensive full fix: big stores/work cost
  strip_nonessential no food cost, but permanently sacrifices convertible
                     growing space and morale (cannibalized decks)
  maintenance/slow   the existing lighter tools still apply and can combine

Public API mirrors incidents_politics. Strain weights are game rules.
"""
from __future__ import annotations

from engine import Effect

SLICE = "bridge_cascade"

# === Strain accumulation (explicit game rules over real engine failures) ===
STRAIN_PER_FAILURE = .22
STRAIN_ACTIVE_FAILURE = .06
STRAIN_LOW_INTEGRITY = .04
STRAIN_DECAY = .06
STRAIN_TRIGGER = .55
INTEGRITY_TRIGGER = .75

OPS = frozenset({"overhaul", "strip_nonessential"})


def ensure(c):
    if SLICE not in c.world.state:
        c.world.state[SLICE] = {"version": 1, "kind": "cascade", "status": "quiet", "strain": 0., "chain": [], "last_failures_total": 0, "cooldown_until": 0, "cascades_total": 0, "history": []}
    return c.world.state[SLICE]


def annual_tick(c):
    state = ensure(c)
    ship = c.world.state.get("ship_integrity") or {}
    failures_total = ship.get("failures_total", 0)
    new = max(0, failures_total - state["last_failures_total"])
    state["last_failures_total"] = failures_total
    if new:
        recent = [f.get("kind") for f in ship.get("failure_history", [])[-new:]]
        state["chain"] = (state["chain"] + [{"year": c.world.tick, "kind": k} for k in recent])[-6:]
    integrity = ship.get("integrity", 1.)
    maintenance_doctrine = "policy_maintenance" in ((c.play or {}).get("policies") or {})
    strain = state["strain"] + STRAIN_PER_FAILURE * new
    strain += STRAIN_ACTIVE_FAILURE if ship.get("active_failure") else 0.
    strain += STRAIN_LOW_INTEGRITY if integrity < .82 else 0.
    strain -= STRAIN_DECAY + (.06 if maintenance_doctrine else 0.)
    state["strain"] = min(1., max(0., strain))
    if state["status"] == "stabilized" and c.world.tick >= state["cooldown_until"]:
        state["status"] = "quiet"
    if state["status"] == "quiet" and c.world.tick >= state["cooldown_until"] and (state["strain"] >= STRAIN_TRIGGER or integrity < INTEGRITY_TRIGGER or new >= 2):
        state["status"] = "active"
        state["cascades_total"] += 1


def should_wake(c):
    state = c.world.state.get(SLICE)
    return bool(state and state["status"] == "active" and c.world.tick >= state["cooldown_until"])


def summary(c):
    state = c.world.state.get(SLICE)
    if not state:
        return None
    ship = c.world.state.get("ship_integrity") or {}
    return {"kind": "cascade", "status": state["status"], "strain": round(state["strain"], 3), "chain": list(state["chain"]), "integrity": round(ship.get("integrity", 1.) * 100, 1), "active_failure": ship.get("active_failure"), "failures_total": ship.get("failures_total", 0), "cascades_total": state["cascades_total"]}


def evidence(c):
    state = ensure(c)
    ship = c.world.state.get("ship_integrity") or {}
    return [{"source": "engine", "strain": round(state["strain"], 3), "failure_chain": list(state["chain"]), "active_failure": ship.get("active_failure"), "active_severity": ship.get("active_severity"), "integrity": round(ship.get("integrity", 1.) * 100, 1), "failures_total": ship.get("failures_total", 0), "note": "Failure kinds, severities and integrity are the engine's own persisted values; strain is an explicit modeled backlog measure, not a measured stress reading."}]


def validate_op(c, op):
    state = c.world.state.get(SLICE) or {}
    if state.get("status") != "active":
        raise ValueError("There is no failure cascade to break. Nothing changed.")


def _clear_failure_effects():
    return [Effect("ship_integrity.active_failure", "set", None), Effect("ship_integrity.active_severity", "set", 0), Effect("ship_integrity.active_years_remaining", "set", 0), Effect("mortality.modifiers.system_failure", "set", 1.0)]


def apply_op(c, op):
    state = ensure(c)
    effects, note = [], {"op": op}
    if op == "overhaul":
        state.update(strain=0., chain=[], status="stabilized", cooldown_until=c.world.tick + 6)
        effects += [Effect("ship_integrity.integrity", "add", .18)] + _clear_failure_effects()
        note["outcome"] = "full overhaul; integrity +18 points; the failure chain is cleared"
    elif op == "strip_nonessential":
        state.update(strain=max(0., state["strain"] - .40), status="stabilized", cooldown_until=c.world.tick + 6)
        # Cannibalized decks: convertible growing space is gone for the voyage.
        c.play["garden_limit"] *= .94
        effects += [Effect("ship_integrity.integrity", "add", .10), Effect("morale.aggregate", "add", -6)] + _clear_failure_effects()
        note["outcome"] = "non-essential decks cannibalized; integrity +10; convertible growing space −6%; morale −6"
    state["history"] = (state["history"] + [{"year": c.world.tick, **note}])[-8:]
    return effects, note


def defer(c):
    ensure(c)["cooldown_until"] = c.world.tick + 2


def resolved(c):
    state = c.world.state.get(SLICE) or {}
    ship = c.world.state.get("ship_integrity") or {}
    return ship.get("integrity", 0.) >= .88 and not ship.get("active_failure") and state.get("strain", 1.) < .5
