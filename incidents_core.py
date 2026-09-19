"""Incident-domain registry and standing policies (intervention economics).

One seam between voyage.py and the four grounded incident domains:

    uprising    incidents_politics — a real faction against the government
    crime       incidents_crime    — organized theft/coercion from the roster
    cascade     incidents_systems  — the engine's failures, chaining
    ai_steward  incidents_ai       — WARDEN contesting authority over stores

Each domain owns one bridge slice, fires from deterministic predicates over
persisted engine state, and mutates the world only through the caller's
commit seam (returned Effects) plus explicit conserved ledgers.

STANDING POLICIES are the steering layer: doctrines the admiral sets while
awake that apply every cryo year with visible ledgered costs, shifting
trajectories BETWEEN wakes instead of only resolving incidents. At most
MAX_POLICIES run at once; committing an active policy op rescinds it.

Public API: ensure, annual_tick, urgent, evidence, summaries, incident_ops,
validate_ops, apply_ops, defer, resolved, active_policies. Offline and
deterministic; no model calls.
"""
from __future__ import annotations

from engine import Effect
import incidents_ai
import incidents_crime
import incidents_politics
import incidents_systems

# Wake priority: lethality/irreversibility first. The outbreak stays above all
# of these in voyage.urgent.
DOMAINS = {"cascade": incidents_systems, "uprising": incidents_politics, "ai_steward": incidents_ai, "crime": incidents_crime}

MAX_POLICIES = 3
# Annual, engine-rooted, and every cost visible in the play ledger. Rates are
# explicit game-design rules; "prevents/suppresses" effects are implemented in
# the named domain modules, which read the active policy set.
POLICIES = {
    "policy_apothecary": {"name": "Standing pharmaceutical rotation", "annual": "medicine +0.30 kg/person; food −0.9%; morale −0.2"},
    "policy_maintenance": {"name": "Standing maintenance doctrine", "annual": "integrity +0.5 points; cascade strain −0.06; food −0.8%; morale −0.3"},
    "policy_assembly": {"name": "Open-ledger assemblies", "annual": "mandate +0.8 points; pressure −0.02; morale +0.4; food −0.5%"},
    "policy_watch": {"name": "Constabulary watch", "annual": "crime formation/growth suppressed; morale −0.6; mandate −0.4 points"},
    "policy_ai_limits": {"name": "Bounded machine authority", "annual": "steward sequester rate ×0.4 while active; integrity −0.2 points (lost autonomic upkeep)"},
}


def ensure(c):
    for module in DOMAINS.values():
        module.ensure(c)
    if c.play is not None:
        c.play.setdefault("policies", {})
        c.play.setdefault("policy_ledger", [])


def active_policies(c):
    return (c.play or {}).get("policies") or {}


def _apply_policies(c):
    """One cryo year of every standing doctrine, costs written to the ledger."""
    play = c.play
    if not play or not play.get("policies"):
        return
    resources = c.world.state["resources"]
    morale = c.world.state.get("morale", {})
    gov = c.world.state.get("governance") or {}
    ship = c.world.state.get("ship_integrity") or {}
    alive = max(0, c.world.state["population"]["alive"])
    entry = {"year": c.world.tick, "applied": {}}
    for policy in sorted(play["policies"]):
        if policy == "policy_apothecary":
            made = alive * .30
            food = resources["food_kg"] * .009
            resources["medicine_kg"] += made
            resources["food_kg"] -= food
            morale["aggregate"] = max(0., morale.get("aggregate", 0.) - .2)
            entry["applied"][policy] = {"medicine_kg": round(made, 1), "food_kg": round(-food, 1), "morale": -.2}
        elif policy == "policy_maintenance":
            food = resources["food_kg"] * .008
            resources["food_kg"] -= food
            ship["integrity"] = min(1., ship.get("integrity", 1.) + .005)
            morale["aggregate"] = max(0., morale.get("aggregate", 0.) - .3)
            entry["applied"][policy] = {"integrity_points": .5, "food_kg": round(-food, 1), "morale": -.3}
        elif policy == "policy_assembly":
            food = resources["food_kg"] * .005
            resources["food_kg"] -= food
            gov["legitimacy"] = min(1., gov.get("legitimacy", .85) + .008)
            gov["pressure"] = max(0., gov.get("pressure", .1) - .02)
            morale["aggregate"] = min(100., morale.get("aggregate", 0.) + .4)
            entry["applied"][policy] = {"mandate_points": .8, "food_kg": round(-food, 1), "morale": .4}
        elif policy == "policy_watch":
            morale["aggregate"] = max(0., morale.get("aggregate", 0.) - .6)
            gov["legitimacy"] = max(0., gov.get("legitimacy", .85) - .004)
            entry["applied"][policy] = {"morale": -.6, "mandate_points": -.4, "effect": "crime suppression"}
        elif policy == "policy_ai_limits":
            ship["integrity"] = max(0., ship.get("integrity", 1.) - .002)
            entry["applied"][policy] = {"integrity_points": -.2, "effect": "steward sequester limited"}
    play["policy_ledger"] = (play["policy_ledger"] + [entry])[-40:]


def annual_tick(c):
    """One voyage year of doctrines and domain progressions. Caller owns save."""
    ensure(c)
    _apply_policies(c)
    for module in DOMAINS.values():
        module.annual_tick(c)


def urgent(c):
    """Pure read: the highest-priority domain demanding the admiral, or None."""
    for kind, module in DOMAINS.items():
        if module.should_wake(c):
            return kind
    return None


def evidence(c, kind):
    return DOMAINS[kind].evidence(c) if kind in DOMAINS else []


def summaries(c):
    return {kind: module.summary(c) for kind, module in DOMAINS.items() if module.summary(c)}


def incident_ops(kind):
    """Domain-specific operation ids for a locked incident of this kind."""
    return set(DOMAINS[kind].OPS) if kind in DOMAINS else set()


def validate_ops(c, ops):
    """Pure. Domain ops need their live domain; policies respect the cap."""
    for op in ops:
        for module in DOMAINS.values():
            if op in module.OPS:
                module.validate_op(c, op)
    settings = [op for op in ops if op in POLICIES and op not in active_policies(c)]
    if settings and len(active_policies(c)) + len(settings) > MAX_POLICIES:
        raise ValueError(f"The crew can sustain at most {MAX_POLICIES} standing doctrines. Rescind one first.")


def apply_ops(c, ops):
    """After validation only. Returns (extra Effects, per-op notes)."""
    effects, notes = [], []
    for op in ops:
        for module in DOMAINS.values():
            if op in module.OPS:
                extra, note = module.apply_op(c, op)
                effects += extra
                notes.append(note)
        if op in POLICIES:
            policies = c.play.setdefault("policies", {})
            if op in policies:
                del policies[op]
                notes.append({"op": op, "outcome": f"doctrine rescinded: {POLICIES[op]['name']}"})
            else:
                policies[op] = {"since": c.world.tick}
                notes.append({"op": op, "outcome": f"standing doctrine set: {POLICIES[op]['name']} ({POLICIES[op]['annual']})"})
    return effects, notes


def defer(c, kind):
    if kind in DOMAINS:
        DOMAINS[kind].defer(c)


def resolved(c, kind):
    return DOMAINS[kind].resolved(c) if kind in DOMAINS else None
