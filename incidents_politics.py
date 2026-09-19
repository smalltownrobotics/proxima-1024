"""Political legitimacy crisis: an actual faction moves against the government.

Grounded in the engine's own factions/tension/governance slices — the movement
is always a real faction with its current share, alienation and intensity, and
every trigger threshold reads persisted engine state. This module owns only the
bridge_uprising slice; engine metrics change through the caller's Effect seam
or the explicit clamped writes below, with each delta named in the op notes.

Resolution paths are genuinely different bargains:
  concede       legitimacy up, movement stands down, food/work cost
  cede_commons  movement stands down hard, but the ship loses growing area
  crackdown     fast suppression that costs mandate/morale and deepens
                alienation, so the next uprising comes sooner

Public API: ensure, annual_tick, should_wake, evidence, summary, validate_op,
apply_op, defer, resolved. All numbers are explicit game-design rules, not
political science predictions.
"""
from __future__ import annotations

from engine import Effect

SLICE = "bridge_uprising"

# === Trigger and escalation constants (game rules, not forecasts) ===
LEGITIMACY_TRIGGER = .42
PRESSURE_TRIGGER = .80
ALIENATION_TRIGGER = .60
MIN_SHARE = .12
ESCALATION_PRESSURE_PER_YEAR = .02
ESCALATION_MORALE_PER_YEAR = 1.
COOLDOWN_STOOD_DOWN = 10
COOLDOWN_SUPPRESSED = 4

OPS = frozenset({"concede", "cede_commons", "crackdown"})


def ensure(c):
    if SLICE not in c.world.state:
        c.world.state[SLICE] = {"version": 1, "kind": "uprising", "status": "quiet", "movement": None, "cooldown_until": 0, "uprisings_total": 0, "suppressed_total": 0, "history": []}
    return c.world.state[SLICE]


def _factions(c):
    return (c.world.state.get("factions") or {}).get("factions") or {}


def _aggrieved(c):
    """The engine faction with the most organized grievance, or None."""
    rows = [(f["alienation"] * (0.5 + f["intensity"]) * max(f["share"], .01), fid, f) for fid, f in _factions(c).items() if f["share"] >= MIN_SHARE]
    if not rows:
        return None, None
    _, fid, f = max(rows, key=lambda r: (r[0], r[1]))
    return fid, f


def _grievance(c):
    """Name the worst actual deficit so evidence explains WHY they moved."""
    s = c.summary()
    candidates = [(s["food_adequacy"] < .95, "short rations"), (s["morale"] < 50, "exhaustion and low morale"), (s["mandate"] < 50, "a government they no longer consent to"), (True, "an unaccountable command structure")]
    return next(reason for hit, reason in candidates if hit)


def annual_tick(c):
    state = ensure(c)
    gov = c.world.state.get("governance") or {}
    if state["status"] == "active":
        # An unanswered movement organizes: pressure and fatigue accumulate.
        gov["pressure"] = min(1., gov.get("pressure", .1) + ESCALATION_PRESSURE_PER_YEAR)
        morale = c.world.state.get("morale", {})
        morale["aggregate"] = max(0., morale.get("aggregate", 0.) - ESCALATION_MORALE_PER_YEAR)
        state["movement"]["years"] += 1
        return
    if state["status"] != "quiet" or c.world.tick < state["cooldown_until"]:
        # stood_down/suppressed re-enter quiet after their cooldown.
        if state["status"] in {"stood_down", "suppressed"} and c.world.tick >= state["cooldown_until"]:
            state["status"] = "quiet"
        return
    fid, faction = _aggrieved(c)
    legitimacy = gov.get("legitimacy", .85)
    pressure = gov.get("pressure", .1)
    triggered = legitimacy < LEGITIMACY_TRIGGER or pressure > PRESSURE_TRIGGER or (faction is not None and faction["alienation"] >= ALIENATION_TRIGGER)
    if not triggered or faction is None:
        return
    state["status"] = "active"
    state["uprisings_total"] += 1
    state["movement"] = {"faction_id": fid, "name": faction["name"], "share": round(faction["share"], 3), "alienation": round(faction["alienation"], 3), "intensity": round(faction["intensity"], 3), "grievance": _grievance(c), "formed_year": c.world.tick, "years": 0}


def should_wake(c):
    state = c.world.state.get(SLICE)
    return bool(state and state["status"] == "active" and c.world.tick >= state["cooldown_until"])


def summary(c):
    state = c.world.state.get(SLICE)
    if not state:
        return None
    gov = c.world.state.get("governance") or {}
    return {"kind": "uprising", "status": state["status"], "movement": dict(state["movement"]) if state["movement"] else None, "legitimacy": round(gov.get("legitimacy", 0.), 3), "pressure": round(gov.get("pressure", 0.), 3), "uprisings_total": state["uprisings_total"], "suppressed_total": state["suppressed_total"]}


def evidence(c):
    state = ensure(c)
    m = state["movement"] or {}
    tension = c.world.state.get("tension") or {}
    return [{"source": "engine", "movement": m, "governance": {"legitimacy": round((c.world.state.get("governance") or {}).get("legitimacy", 0.), 3), "pressure": round((c.world.state.get("governance") or {}).get("pressure", 0.), 3), "type": (c.world.state.get("governance") or {}).get("type")}, "tension_max_pair": tension.get("max_pair_ids"), "tension_max": round(tension.get("max_pair", 0.), 3), "note": "Faction shares, alienation and pressure are persisted engine state; thresholds are explicit game rules."}]


def validate_op(c, op):
    state = c.world.state.get(SLICE) or {}
    if state.get("status") != "active":
        raise ValueError("There is no active political movement to answer. Nothing changed.")


def apply_op(c, op):
    """Mutate the movement/faction record; return engine Effects for the caller's single committed event."""
    state = ensure(c)
    movement = state["movement"]
    faction = _factions(c).get(movement["faction_id"]) if movement else None
    gov = c.world.state.get("governance") or {}
    effects, note = [], {"op": op, "movement": movement["name"] if movement else None}
    if op == "concede":
        state["status"] = "stood_down"
        state["cooldown_until"] = c.world.tick + COOLDOWN_STOOD_DOWN
        if faction:
            faction["alienation"] = max(.02, faction["alienation"] - .15)
            faction["intensity"] = max(.05, faction["intensity"] - .10)
        # Standing down ends the standoff: pressure falls to a governable level
        # (the engine is free to rebuild it later from real conditions).
        gov["pressure"] = min(gov.get("pressure", .1), .45)
        effects += [Effect("governance.legitimacy", "add", .08), Effect("morale.aggregate", "add", 4)]
        note["outcome"] = "movement stood down; seats granted; alienation eased"
    elif op == "cede_commons":
        state["status"] = "stood_down"
        state["cooldown_until"] = c.world.tick + COOLDOWN_STOOD_DOWN
        if faction:
            faction["alienation"] = max(.02, faction["alienation"] - .25)
        gov["pressure"] = min(gov.get("pressure", .1), .50)
        # The uprising's real price: finite interior growing area, forever.
        effects += [Effect("resources.agricultural_area_m2", "mul", .94), Effect("morale.aggregate", "add", 6), Effect("governance.legitimacy", "add", .05)]
        note["outcome"] = "movement stood down; 6% of growing area ceded to its commons"
    elif op == "crackdown":
        state["status"] = "suppressed"
        state["suppressed_total"] += 1
        state["cooldown_until"] = c.world.tick + COOLDOWN_SUPPRESSED
        if faction:
            faction["intensity"] = max(.05, faction["intensity"] - .30)
            faction["alienation"] = min(1., faction["alienation"] + .10)
        gov["pressure"] = min(gov.get("pressure", .1), .40)
        effects += [Effect("governance.legitimacy", "add", -.10), Effect("morale.aggregate", "add", -8)]
        note["outcome"] = "movement suppressed; mandate and morale paid for it; alienation deepened"
    state["history"] = (state["history"] + [{"year": c.world.tick, **note}])[-8:]
    return effects, note


def defer(c):
    """Accepting the risk delays the reckoning; the movement keeps organizing."""
    state = ensure(c)
    state["cooldown_until"] = c.world.tick + 3


def resolved(c):
    state = c.world.state.get(SLICE) or {}
    pressure = (c.world.state.get("governance") or {}).get("pressure", 0.)
    return state.get("status") in {"stood_down", "suppressed"} and pressure <= .55
