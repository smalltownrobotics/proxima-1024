"""V2 experience state: locked incidents, immediate decisions, and captain cryo.

No model may write arbitrary state. Operations are finite, costs are computed here,
and resolution is a measured predicate. Captain cryo never freezes the population.
"""
from collections import Counter
import copy
import math
import secrets

from engine import Effect, Event, ROOMS, refresh_generations
import outbreak

# === Game-design bounds, explicit rather than claimed physical forecasts ===
OPERATIONS = {
    "medical": {"name": "Replenish medical stores", "room": "medical", "effect": "+3 years of medicine", "food_cost": .16, "work": 24},
    "gardens": {"name": "Expand the growing decks", "room": "gardens", "effect": "+10% growing area", "food_cost": .08, "work": 30},
    "ration": {"name": "Recover food losses", "room": "gardens", "effect": "+6% production efficiency; morale −4; no food expenditure", "food_cost": 0, "work": 18},
    "maintenance": {"name": "Repair the ship", "room": "drive", "effect": "integrity +10 points; repair the active machinery failure", "food_cost": .08, "work": 28},
    "charter": {"name": "Negotiate a shared plan", "room": "commons", "effect": "morale +8; legitimacy +10 points", "food_cost": .03, "work": 18},
    "rest": {"name": "Give the crew recovery time", "room": "habitat", "effect": "morale +12; integrity −1 point", "food_cost": .03, "work": 12},
    "slow": {"name": "Delay arrival for repairs", "room": "drive", "effect": "journey +2 years; integrity +12 points", "food_cost": .02, "work": 18},
    "emergency": {"name": "Impose emergency authority", "room": "bridge", "effect": "integrity +8; morale −5; legitimacy −8", "food_cost": .04, "work": 24},
    "hold": {"name": "Accept the risk", "room": "bridge", "effect": "No material improvement; recorded risk acceptance", "food_cost": 0, "work": 0},
    "isolate": {"name": "Isolate the affected decks", "room": "habitat", "effect": "28-day containment: reduce transmission; sacrifice food production and morale", "food_cost": 0, "work": 32},
    "surge_care": {"name": "Mobilize emergency care", "room": "medical", "effect": "28-day emergency wards: expand care capacity and spend finite medical reserves", "food_cost": 0, "work": 48},
    "protect_food": {"name": "Keep production running", "room": "gardens", "effect": "28 days of normal production: conserve stores but accept greater transmission", "food_cost": 0, "work": 12},
}
INCIDENTS = {
    "health": {"title": "The medical reserve is running out.", "room": "medical", "goal": "Restore at least 2.5 years of medicine.", "suggestion": "Use our fabrication allowance to replenish medicine. Keep the cost fair across the ship."},
    "food": {"title": "We are eating into the buffer.", "room": "gardens", "goal": "Add growing capacity or reduce production losses.", "suggestion": "Convert spare interior space into growing decks, with a fair construction rotation."},
    "repair": {"title": "The ship needs more than another patch.", "room": "drive", "goal": "Repair the active failure and restore integrity to 92%.", "suggestion": "Prioritize the damaged machinery. Use stores for repairs before asking people to work harder."},
    "trust": {"title": "The people carrying us need a say.", "room": "commons", "goal": "Bring morale to 65 and legitimacy to 65%.", "suggestion": "Hold an open vote on the plan, reduce optional duties, and explain the tradeoffs honestly."},
    "review": {"title": "The watch has flagged a decision.", "room": "commons", "goal": "Review the ship's condition and choose the next intervention.", "suggestion": "Give the crew a voice in the next stretch. Reaffirm what we are working toward."},
    "outbreak": {"title": "A plague is spreading through the ship.", "room": "medical", "goal": "Contain the outbreak. Every response advances 28 days of life-or-death consequences.", "suggestion": "Isolate the affected decks. Divert our medical reserves to emergency care. Save lives, even if the stores take the hit."},
}


def ensure(c):
    if c.play is None:
        c.play = {"version": 2, "mode": "ready", "revision": 0, "incident": None, "work": 100, "garden_limit": c.config["ship"]["agricultural_area_m2"] * 1.65, "last_decision": None, "timeline": [c.summary()], "wake_checks": [], "resolved": 0, "request_ids": {}}
    c.play.setdefault("distance_traveled_ly", min(c.config["mission"]["distance_ly"], c.world.tick * c.config["mission"]["velocity_c"]))
    c.play.setdefault("travel_hold_years", 0)
    c.play.setdefault("captain_cycles", 0)
    c.play.setdefault("captain_awake_days", 0)
    c.play.setdefault("captain_slept_years", 0)
    c.play.setdefault("consultations", [])
    c.play.setdefault("advisory_audits", [])
    outbreak.ensure(c)
    return c.play


def open_incident(c, kind, evidence=None):
    p = ensure(c)
    info = copy.deepcopy(INCIDENTS[kind])
    info.update(id=secrets.token_hex(6), kind=kind, opened_year=c.world.tick, evidence=evidence or [], baseline={"area": c.world.read("resources.agricultural_area_m2"), "yield": c.world.read("resources.yield_per_m2")}, resolved=False, accepted_risk=False)
    p.update(mode="awake", incident=info, work=100)
    c.focus = info["room"]
    return info


def urgent(c):
    epidemic = outbreak.summary(c)
    if epidemic and epidemic.get("status") == "active":
        return "outbreak"
    s = c.summary()
    if s["integrity"] < 85 or s["active_failure"]:
        return "repair"
    if s["medicine_years"] < 2:
        return "health"
    if s["food_days"] < 100 or s["food_adequacy"] < .92:
        return "food"
    if s["morale"] < 48 or s["mandate"] < 50:
        return "trust"
    return None


def barriers(c):
    p, s = ensure(c), c.summary()
    area = c.world.read("resources.agricultural_area_m2")
    return {"food_days": s["food_days"], "protected_food_days": 45, "work_remaining": p["work"], "garden_area_m2": round(area), "garden_limit_m2": round(p["garden_limit"]), "medicine_years": s["medicine_years"], "integrity": s["integrity"], "morale": s["morale"], "mandate": s["mandate"], "outbreak": outbreak.summary(c), "scope": "Explicit prototype resource limits; not a flight feasibility or clinical calculation"}


def available_operations(c):
    """Astra sees only the mechanics meaningful in this locked incident."""
    active = (ensure(c).get("incident") or {}).get("kind") == "outbreak"
    keys = {"isolate", "surge_care", "protect_food", "hold"} if active else set(OPERATIONS) - {"isolate", "surge_care", "protect_food"}
    return {k: v for k, v in OPERATIONS.items() if k in keys}


def validate_plan(c, plan):
    if not isinstance(plan, dict):
        raise ValueError("Astra did not return a valid operation plan. Nothing changed.")
    operations = plan.get("operations", [])
    if not isinstance(operations, list) or not 1 <= len(operations) <= 3:
        raise ValueError("Describe one achievable order, or up to three connected operations.")
    if any(not isinstance(op, str) for op in operations) or len(set(operations)) != len(operations) or any(op not in OPERATIONS for op in operations):
        raise ValueError("This order includes an unsupported operation.")
    if "hold" in operations and len(operations) > 1:
        raise ValueError("Accepting the risk cannot be mixed with an active repair order.")
    if any(op not in available_operations(c) for op in operations):
        raise ValueError("Use an operation available for the current incident. During an outbreak, choose containment, emergency care, continued production, or explicit waiting.")
    b = barriers(c)
    work = sum(OPERATIONS[op]["work"] for op in operations)
    fraction = math.prod(1 - OPERATIONS[op]["food_cost"] for op in operations)
    if work > b["work_remaining"]:
        raise ValueError(f"This asks for {work} work units; only {b['work_remaining']} remain in this response window. Choose a smaller intervention.")
    if b["food_days"] * fraction < 45 and fraction < 1:
        raise ValueError("That would spend the protected 45-day food buffer. Recover production losses without spending food, or explicitly accept the risk.")
    if "gardens" in operations and b["garden_area_m2"] * 1.1 > b["garden_limit_m2"]:
        raise ValueError("No more convertible growing space remains in this hull. Recover production losses instead.")
    validated = {"operations": operations, "work_cost": work, "food_cost_percent": round((1 - fraction) * 100, 1), "food_multiplier": fraction, "effects": [OPERATIONS[op]["effect"] for op in operations]}
    if (ensure(c).get("incident") or {}).get("kind") == "outbreak":
        validated["outbreak_response"] = outbreak.validate(c, operations)
    return validated


def check_resolution(c, operations):
    p, s = ensure(c), c.summary()
    incident = p["incident"]
    if incident["kind"] == "outbreak":
        # Waiting must play out the lethal course, never dismiss an active plague.
        incident["resolved"] = outbreak.summary(c).get("status") == "contained"
        incident["accepted_risk"] = False
    elif "hold" in operations:
        incident.update(resolved=True, accepted_risk=True)
    elif incident["kind"] == "health":
        incident["resolved"] = s["medicine_years"] >= 2.5
    elif incident["kind"] == "repair":
        incident["resolved"] = s["integrity"] >= 92 and not s["active_failure"]
    elif incident["kind"] == "trust":
        incident["resolved"] = s["morale"] >= 65 and s["mandate"] >= 65
    elif incident["kind"] == "food":
        incident["resolved"] = c.world.read("resources.agricultural_area_m2") >= incident["baseline"]["area"] * 1.09 or c.world.read("resources.yield_per_m2") >= incident["baseline"]["yield"] * 1.05
    else:
        incident["resolved"] = True
    if incident["resolved"]:
        p["mode"] = "ready"
        p["resolved"] += 1


def commit(c, decision, plan, reactions, audit):
    p = ensure(c)
    if p["mode"] != "awake" or c.status != "in_flight":
        raise ValueError("There is no active incident to decide.")
    validated = validate_plan(c, plan)
    before = c.summary()
    epidemic = outbreak.apply_response(c, validated["operations"], reactions) if p["incident"]["kind"] == "outbreak" else None
    effects = [Effect("resources.food_kg", "mul", validated["food_multiplier"])]
    for op in validated["operations"]:
        if op == "medical":
            effects.append(Effect("resources.medicine_kg", "add", before["crew"] * .5 * 3))
        elif op == "gardens":
            effects += [Effect("resources.agricultural_area_m2", "mul", 1.10), Effect("morale.aggregate", "add", -3)]
        elif op == "ration":
            effects += [Effect("resources.yield_per_m2", "mul", 1.06), Effect("morale.aggregate", "add", -4)]
        elif op == "maintenance":
            effects.append(Effect("ship_integrity.integrity", "add", .10))
            effects += [Effect("ship_integrity.active_failure", "set", None), Effect("ship_integrity.active_severity", "set", 0), Effect("ship_integrity.active_years_remaining", "set", 0)]
            effects.append(Effect("mortality.modifiers.system_failure", "set", 1.0))
        elif op == "charter":
            effects += [Effect("morale.aggregate", "add", 8), Effect("governance.legitimacy", "add", .10)]
        elif op == "rest":
            effects += [Effect("morale.aggregate", "add", 12), Effect("ship_integrity.integrity", "add", -.01)]
        elif op == "slow":
            effects += [Effect("ship_integrity.voyage_extension_years", "add", 2), Effect("ship_integrity.integrity", "add", .12)]
        elif op == "emergency":
            effects += [Effect("ship_integrity.integrity", "add", .08), Effect("morale.aggregate", "add", -5), Effect("governance.legitimacy", "add", -.08)]
    n = max(1, len(reactions))
    means = {k: sum(r["probabilities"][k] for r in reactions.values()) / n for k in ("support", "question", "oppose")}
    social = means["support"] - means["oppose"]
    effects += [Effect("morale.aggregate", "add", social * 5), Effect("governance.legitimacy", "add", social * .025)]
    effects += [Effect("morale.aggregate", "clamp", [0, 100]), Effect("governance.legitimacy", "clamp", [0, 1]), Effect("ship_integrity.integrity", "clamp", [0, 1])]
    c.world.events.emit(Event(kind="captain_order", source="bridge", tick=c.world.tick, payload={"decision": decision, "plan": validated}, effects=tuple(effects)))
    c.world.events.dispatch_pending(c.world)
    if "slow" in validated["operations"]:
        p["travel_hold_years"] += 2
    c.reactions = reactions
    c.jev_runs.append(audit)
    p["work"] -= validated["work_cost"]
    p["revision"] += 1
    check_resolution(c, validated["operations"])
    if epidemic:
        p["captain_awake_days"] += epidemic["days"]
        # Work is a per-response-window capacity: the next order is another 28
        # days, not a free same-instant reset. Supplies and deaths persist.
        if not p["incident"]["resolved"] and c.status == "in_flight":
            p["work"] = 100
        if c.summary()["crew"] == 0:
            c.status = "lost"
            p["mode"] = "ended"
    record = {"year": c.world.tick, "action": "+".join(validated["operations"]), "title": plan.get("interpretation", "Order executed"), "decision": decision, "before": before, "after": c.summary(), "events": [], "support": round(means["support"], 3), "plan": validated, "revision": p["revision"]}
    if epidemic:
        record["outbreak_result"] = {k: v for k, v in epidemic.items() if k != "frames"}
    c.history.append(record)
    p["last_decision"] = {**record, "resolved": p["incident"]["resolved"], "accepted_risk": p["incident"]["accepted_risk"], "distributions": means}
    p["timeline"].append(c.summary())
    c.save()
    return {**record, "outbreak_frames": epidemic["frames"]} if epidemic else record


def travel_tick(c):
    p = ensure(c)
    before = c.summary()
    snap = c.orch.step()
    refresh_generations(c.world)
    outbreak.start_if_due(c)
    if p["mode"] == "cryo":
        p["captain_slept_years"] += 1
    p["revision"] += 1
    after = c.summary()
    # Straight-line cruise visualization: extensions consume time, never reverse
    # distance already traveled. It deliberately omits acceleration/braking.
    p["travel_hold_years"] += max(0, after["arrival_year"] - before["arrival_year"])
    held = min(1, p["travel_hold_years"])
    p["travel_hold_years"] -= held
    p["distance_traveled_ly"] = min(c.config["mission"]["distance_ly"], p["distance_traveled_ly"] + c.config["mission"]["velocity_c"] * (1 - held))
    if after["crew"] == 0:
        c.status = "lost"
    elif after["remaining"] == 0:
        c.status = "arrived"
        p["distance_traveled_ly"] = c.config["mission"]["distance_ly"]
    if c.status != "in_flight":
        p["mode"] = "ended"
    p["timeline"].append(after)
    return {"before": before, "after": after, "births": after["births"] - before["births"], "deaths": after["deaths"] - before["deaths"], "events": snap.get("events", [])}
