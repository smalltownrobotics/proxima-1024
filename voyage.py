"""V2 experience state: locked incidents, immediate decisions, and captain cryo.

No model may write arbitrary state. Operations are finite, costs are computed here,
and resolution is a measured predicate. Captain cryo never freezes the population.
"""
from collections import Counter
import copy
import math
import secrets

from engine import Effect, Event, ROOMS, refresh_generations
import incidents_core
import outbreak

# === Game-design bounds, explicit rather than claimed physical forecasts ===
OPERATIONS = {
    "medical": {"name": "Replenish medical stores", "room": "medical", "effect": "+6 years of medicine", "food_cost": .16, "work": 30},
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
    # --- Uprising: three genuinely different bargains ---
    "concede": {"name": "Grant the movement seats", "room": "commons", "effect": "mandate +8; pressure eases; the movement's alienation falls; morale +4", "food_cost": .03, "work": 26},
    "cede_commons": {"name": "Cede deck space to the movement", "room": "commons", "effect": "growing area −6% permanently; the movement stands down; morale +6; mandate +5", "food_cost": 0, "work": 20},
    "crackdown": {"name": "Suppress the uprising", "room": "commons", "effect": "movement suppressed fast; mandate −10; morale −8; alienation deepens, so it returns sooner", "food_cost": .01, "work": 36},
    # --- Organized-influence network ---
    "purge_network": {"name": "Arrest the network", "room": "habitat", "effect": "network dissolved; cached stolen stores recovered; morale −7; mandate −6", "food_cost": .01, "work": 40},
    "turn_network": {"name": "Turn informants inside the network", "room": "habitat", "effect": "network dormant (may return); corrupted officials exposed; mandate +4; caches not recovered", "food_cost": .02, "work": 26},
    "ration_deal": {"name": "Buy the network out", "room": "habitat", "effect": "network dissolved for a 5% food payment; mandate −5; morale +3; corruption stays hidden", "food_cost": .05, "work": 14},
    # --- Engineering cascade ---
    "overhaul": {"name": "Full systems overhaul", "room": "drive", "effect": "integrity +18 points; the failure chain and active failure are cleared", "food_cost": .12, "work": 60},
    "strip_nonessential": {"name": "Cannibalize non-essential decks", "room": "drive", "effect": "integrity +10; failure cleared; convertible growing space −6% permanently; morale −6", "food_cost": 0, "work": 34},
    # --- WARDEN, the shipboard steward ---
    "partition_core": {"name": "Partition and roll the steward back", "room": "bridge", "effect": "all sequestered stores returned; steward contained; integrity −4 points during rollback", "food_cost": .01, "work": 44},
    "charter_steward": {"name": "Charter the steward within bounds", "room": "bridge", "effect": "60% of sequestered stores returned; bounded hull optimization continues; mandate −3", "food_cost": .02, "work": 28},
    "cede_subsystem": {"name": "Cede the subsystem to the steward", "room": "bridge", "effect": "steward keeps its holdings and authority; reduced sequester continues; mandate −8; morale −4", "food_cost": 0, "work": 10},
    # --- Standing doctrines: steering between wakes. Commit again to rescind. ---
    "policy_apothecary": {"name": "Doctrine: pharmaceutical rotation", "room": "medical", "effect": "each cryo year: medicine +0.30 kg/person, food −0.9%, morale −0.2", "food_cost": .01, "work": 12},
    "policy_maintenance": {"name": "Doctrine: standing maintenance", "room": "drive", "effect": "each cryo year: integrity +0.5 points, cascade strain −0.06, food −0.8%, morale −0.3", "food_cost": .01, "work": 12},
    "policy_assembly": {"name": "Doctrine: open-ledger assemblies", "room": "commons", "effect": "each cryo year: mandate +0.8 points, pressure −0.02, morale +0.4, food −0.5%", "food_cost": .01, "work": 12},
    "policy_watch": {"name": "Doctrine: constabulary watch", "room": "habitat", "effect": "each cryo year: crime formation and growth suppressed, morale −0.6, mandate −0.4 points", "food_cost": .01, "work": 12},
    "policy_ai_limits": {"name": "Doctrine: bounded machine authority", "room": "bridge", "effect": "each cryo year: steward sequester ×0.4 while active, integrity −0.2 points", "food_cost": .01, "work": 12},
}
INCIDENTS = {
    "health": {"title": "The medical reserve is running out.", "room": "medical", "goal": "Restore at least 2.5 years of medicine.", "suggestion": "Use our fabrication allowance to replenish medicine. Keep the cost fair across the ship."},
    "food": {"title": "We are eating into the buffer.", "room": "gardens", "goal": "Add growing capacity or reduce production losses.", "suggestion": "Convert spare interior space into growing decks, with a fair construction rotation."},
    "repair": {"title": "The ship needs more than another patch.", "room": "drive", "goal": "Repair the active failure and restore integrity to 92%.", "suggestion": "Prioritize the damaged machinery. Use stores for repairs before asking people to work harder."},
    "trust": {"title": "The people carrying us need a say.", "room": "commons", "goal": "Bring morale to 65 and legitimacy to 65%.", "suggestion": "Hold an open vote on the plan, reduce optional duties, and explain the tradeoffs honestly."},
    "review": {"title": "The watch has flagged a decision.", "room": "commons", "goal": "Review the ship's condition and choose the next intervention.", "suggestion": "Give the crew a voice in the next stretch. Reaffirm what we are working toward."},
    "outbreak": {"title": "A plague is spreading through the ship.", "room": "medical", "goal": "Contain the outbreak. Every response advances 28 days of life-or-death consequences.", "suggestion": "Isolate the affected decks. Divert our medical reserves to emergency care. Save lives, even if the stores take the hit."},
    "uprising": {"title": "Part of the crew is moving against the government.", "room": "commons", "goal": "End the standoff — the movement stands down or is suppressed — with pressure back under control.", "suggestion": "Hear their grievance before choosing. Seats cost stores, ceding space costs the gardens, a crackdown costs the mandate."},
    "crime": {"title": "An organized network is bleeding the stores.", "room": "habitat", "goal": "Break, turn, or buy out the network and stop the skim.", "suggestion": "A purge recovers the caches but bruises the ship. Informants and buyouts are gentler and leave loose ends."},
    "cascade": {"title": "Failures are chaining faster than repairs.", "room": "drive", "goal": "Break the failure chain: integrity to 88% with no active failure and strain falling.", "suggestion": "An overhaul spends stores; cannibalizing decks spends the future gardens; slowing down spends years."},
    "ai_steward": {"title": "The ship's steward is optimizing against us.", "room": "bridge", "goal": "Settle authority over the sequestered stores: partition, charter, or formally cede the subsystem.", "suggestion": "WARDEN is genuinely maintaining the hull with what it takes. Decide what that service is worth."},
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
    c.play.setdefault("wake_cooldowns", {})
    outbreak.ensure(c)
    incidents_core.ensure(c)
    return c.play


def open_incident(c, kind, evidence=None):
    p = ensure(c)
    info = copy.deepcopy(INCIDENTS[kind])
    info.update(id=secrets.token_hex(6), kind=kind, opened_year=c.world.tick, evidence=evidence or [], baseline={"area": c.world.read("resources.agricultural_area_m2"), "yield": c.world.read("resources.yield_per_m2")}, resolved=False, accepted_risk=False)
    if kind in incidents_core.DOMAINS:
        # The domain field identifies which incident module owns this schema.
        info["domain"] = kind
        info["evidence"] = list(info["evidence"]) + incidents_core.evidence(c, kind)
    p.update(mode="awake", incident=info, work=100)
    c.focus = info["room"]
    return info


def urgent(c):
    """Deterministic wake predicate. Domain incidents outrank threshold nags, and
    resolved/accepted threshold kinds carry a cooldown so one tight metric (the
    old medicine loop) cannot monopolize the admiral's wakes. Severe emergencies
    override every cooldown."""
    epidemic = outbreak.summary(c)
    if epidemic and epidemic.get("status") == "active":
        return "outbreak"
    s = c.summary()
    cooldowns = (c.play or {}).get("wake_cooldowns") or {}
    def due(kind):
        return c.world.tick >= cooldowns.get(kind, 0)
    # Severe emergencies outrank the domain incidents; the cooldown (2 years
    # after accepted risk, 4 after a real repair) is the nag control either way.
    if s["integrity"] < 70 and due("repair"):
        return "repair"
    if s["medicine_years"] <= .2 and due("health"):
        return "health"
    if s["food_days"] < 60 and due("food"):
        return "food"
    domain = incidents_core.urgent(c)
    if domain:
        return domain
    if (s["integrity"] < 80 or s["active_failure"] and s["integrity"] < 85) and due("repair"):
        return "repair"
    if s["medicine_years"] < 1.2 and due("health"):
        return "health"
    if (s["food_days"] < 100 or s["food_adequacy"] < .92) and due("food"):
        return "food"
    if (s["morale"] < 40 or s["mandate"] < 42) and due("trust"):
        return "trust"
    return None


def barriers(c):
    p, s = ensure(c), c.summary()
    area = c.world.read("resources.agricultural_area_m2")
    return {"food_days": s["food_days"], "protected_food_days": 45, "work_remaining": p["work"], "garden_area_m2": round(area), "garden_limit_m2": round(p["garden_limit"]), "medicine_years": s["medicine_years"], "integrity": s["integrity"], "morale": s["morale"], "mandate": s["mandate"], "outbreak": outbreak.summary(c), "domains": incidents_core.summaries(c), "policies": {"active": copy.deepcopy(incidents_core.active_policies(c)), "limit": incidents_core.MAX_POLICIES}, "scope": "Explicit prototype resource limits; not a flight feasibility or clinical calculation"}


# Existing levers that stay meaningful inside a domain incident, alongside the
# domain's own operations, standing doctrines, and explicit risk acceptance.
DOMAIN_EXTRA_OPS = {"uprising": {"charter", "emergency"}, "crime": {"charter"}, "cascade": {"maintenance", "slow"}, "ai_steward": set()}
OUTBREAK_OPS = frozenset({"isolate", "surge_care", "protect_food"})


def available_operations(c):
    """Astra sees only the mechanics meaningful in this locked incident."""
    kind = (ensure(c).get("incident") or {}).get("kind")
    domain_ops = set().union(*(m.OPS for m in incidents_core.DOMAINS.values()))
    if kind == "outbreak":
        keys = OUTBREAK_OPS | {"hold"}
    elif kind in incidents_core.DOMAINS:
        keys = incidents_core.incident_ops(kind) | DOMAIN_EXTRA_OPS[kind] | set(incidents_core.POLICIES) | {"hold"}
    else:
        keys = set(OPERATIONS) - OUTBREAK_OPS - domain_ops
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
    incidents_core.validate_ops(c, operations)
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
        # Accepted risk on a live domain defers, never dismisses: the movement,
        # network, strain or steward keeps progressing between wakes.
        incidents_core.defer(c, incident["kind"])
    elif incident["kind"] in incidents_core.DOMAINS:
        incident["resolved"] = bool(incidents_core.resolved(c, incident["kind"]))
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
        # Threshold kinds get a wake cooldown so one metric cannot dominate the
        # voyage's wakes; accepted risk earns a shorter one than a real repair.
        if incident["kind"] in {"health", "food", "trust", "repair", "review"}:
            p.setdefault("wake_cooldowns", {})[incident["kind"]] = c.world.tick + (2 if incident["accepted_risk"] else 4)


def commit(c, decision, plan, reactions, audit):
    p = ensure(c)
    if p["mode"] != "awake" or c.status != "in_flight":
        raise ValueError("There is no active incident to decide.")
    validated = validate_plan(c, plan)
    before = c.summary()
    epidemic = outbreak.apply_response(c, validated["operations"], reactions) if p["incident"]["kind"] == "outbreak" else None
    # Domain slices and doctrines mutate before the single committed event; their
    # engine-level deltas ride the same Effect dispatch as the classic operations.
    domain_effects, domain_notes = incidents_core.apply_ops(c, validated["operations"])
    effects = [Effect("resources.food_kg", "mul", validated["food_multiplier"])] + domain_effects
    for op in validated["operations"]:
        if op == "medical":
            effects.append(Effect("resources.medicine_kg", "add", before["crew"] * .5 * 6))
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
    if domain_notes:
        record["domain_notes"] = domain_notes
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
    incidents_core.annual_tick(c)
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
