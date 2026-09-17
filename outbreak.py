"""Bounded fictional outbreak, owned by the bridge rather than a model.

This is a game mechanic, not an epidemiological model or medical guidance. An
explicit scenario schedules onset; seeded random draws and the captain's policy
determine its course. Jev supplies cooperation probabilities, never casualties.

Public API: ensure/arm, start_if_due, summary, health, validate, apply_response.
The private world slice and its dedicated RNG fork are covered by Campaign's
existing save/load. Days are a tactical clock nested inside an annual voyage
tick. No annual population/resource tick runs during an unresolved outbreak.

Invariants: only living roster members can become cases; deaths flip their actual
alive flag once, release partners, increment the real death counters, and preserve
the roster record. Invalid plans or incomplete Jev distributions mutate nothing.
No response creates medicine, consumes negative stocks, or silently ends a crisis.
"""
from __future__ import annotations

import copy
import math


# === Explicit abstract game rules ===
SLICE = "bridge_outbreak"
RNG_FORK = "bridge_outbreak_v1"
WINDOW_DAYS = 28
INITIAL_FRACTION = .065
FOOD_KG_PER_PERSON_DAY = 1.2
MEDICINE_PER_CARE_DAY = .36
MEDICINE_SETUP_PER_PERSON = .12
ALLOWED = frozenset({"isolate", "surge_care", "protect_food", "hold"})
RULES = {
    "scope": "Fictional game rates, not clinical predictions.",
    "onset": "Scheduled scenario; individual cases and outcomes use persisted seeded randomness.",
    "cooperation": "Per-person execution = support + 0.45 × question + 0.10 × oppose; all probabilities are used.",
    "isolation": "Isolation reduces contacts; stronger cooperation improves the reduction and costs food production and morale.",
    "care": "Surge-care staffing scales with cooperation; each treated person spends finite medicine and has lower modeled death risk.",
    "clock": "Each order simulates 28 tactical days inside the current voyage year; cryo is blocked until no active infections remain.",
    "outcome": "Code draws infection, recovery and mortality; Jev does not invent deaths or determine an ending.",
}


# === Scenario lifecycle and read-only public contract ===
def ensure(c):
    scenario = c.config.get("scenario") or {}
    if not isinstance(scenario, dict) or scenario.get("kind") != "outbreak":
        return None
    if SLICE not in c.world.state:
        c.world.state[SLICE] = {
            "version": 1, "kind": "outbreak", "title": "The shipboard plague",
            "status": "armed", "onset_year": int(scenario.get("onset_year", 4)),
            "started_year": None, "day": 0, "cases": {}, "cohort": [],
            "initial_infected": 0, "medicine_spent_kg": 0., "food_spent_kg": 0.,
            "last_policy": [], "compliance": None, "care_capacity": 0,
            "care_delivered": 0, "last_response_days": 0,
        }
    return c.world.state[SLICE]


def arm(c, onset_year=4):
    """Opt in a new voyage. Re-arming an existing scenario never resets it."""
    if not isinstance(onset_year, int) or isinstance(onset_year, bool) or onset_year < 0:
        raise ValueError("The scenario needs a nonnegative whole onset year.")
    existing = c.world.state.get(SLICE)
    if existing:
        return existing
    c.config["scenario"] = {"kind": "outbreak", "onset_year": onset_year, "version": 1}
    return ensure(c)


def _people(c):
    # Save files turn mapping keys into strings; normal Campaign.load restores
    # ints. String indexing here keeps both representations equivalent.
    return {str(pid): person for pid, person in c.world.state["population"]["people"].items()}


def _new_case(rng, day, already_ill=False):
    elapsed = rng.randint(3, 8) if already_ill else 0
    return {
        "status": "infected", "day_infected": day - elapsed,
        "day_resolved": None, "duration": rng.randint(12, 19),
        "severe": rng.random() < .28,
    }


def start_if_due(c):
    """Activate once at/after the configured year, returning whether onset occurred."""
    state = ensure(c)
    if not state or state["status"] != "armed" or c.world.tick < state["onset_year"]:
        return False
    people = _people(c)
    cohort = sorted((pid for pid, person in people.items() if person["alive"]), key=int)
    state["cohort"] = cohort
    state["started_year"] = c.world.tick
    if not cohort:
        state["status"] = "extinct"
        return False
    rng = c.world.rng.fork(RNG_FORK)
    selected = cohort.copy()
    rng.shuffle(selected)
    n = max(1, round(len(cohort) * INITIAL_FRACTION))
    state["cases"] = {pid: _new_case(rng, 0, already_ill=True) for pid in selected[:n]}
    state["initial_infected"] = n
    state["status"] = "active"
    return True


def health(c):
    """Public cohort states, including casualties; no hidden case timers or odds."""
    state = c.world.state.get(SLICE)
    if not state:
        return {}
    people = _people(c)
    result = {}
    for pid in state["cohort"]:
        person = people.get(pid)
        if person is None:
            continue
        case = state["cases"].get(pid)
        status = case["status"] if case else "susceptible"
        if not person["alive"]:
            status = "dead"
        elif case and status == "infected" and case["severe"] and state["day"] - case["day_infected"] >= 4:
            status = "critical"
        result[pid] = {"status": status, "day_infected": case["day_infected"] if case else None, "day_resolved": case["day_resolved"] if case else None}
    return result


def summary(c):
    """Read only. Serving a page must neither activate a scenario nor draw RNG."""
    state = c.world.state.get(SLICE)
    if not state:
        return None
    people = _people(c)
    public_health = health(c)
    infected = sum(row["status"] in {"infected", "critical"} for row in public_health.values())
    critical = sum(row["status"] == "critical" for row in public_health.values())
    # An unrelated death after containment must not become an outbreak casualty.
    deaths = sum(case["status"] == "dead" for case in state["cases"].values())
    recovered = sum(case["status"] == "recovered" for case in state["cases"].values())
    return {
        **{key: copy.deepcopy(state[key]) for key in ("version", "kind", "title", "status", "onset_year", "started_year", "day", "initial_infected", "last_policy", "compliance", "care_capacity", "care_delivered", "last_response_days")},
        "active": state["status"] == "active", "resolved": state["status"] == "contained",
        "population": sum(bool(person["alive"]) for person in people.values()),
        "infected": infected, "critical": critical, "recovered": recovered,
        "deaths": deaths, "ever_infected": len(state["cases"]),
        "medicine_spent_kg": round(state["medicine_spent_kg"], 2),
        "food_spent_kg": round(state["food_spent_kg"], 2),
        "cooperation": state["compliance"]["mean"] if state["compliance"] else None,
        "rules": copy.deepcopy(RULES),
    }


# === Validation is pure: rejecting a plan must be a complete no-op ===
def _food_rate(ops):
    # Opportunity cost: normal annual food accounting is not replayed. These are
    # additional lost harvest/support costs from this tactical intervention.
    return (.65 if "isolate" in ops else .02 if "protect_food" in ops else 0) + (.12 if "surge_care" in ops else 0)


def validate(c, ops):
    state = c.world.state.get(SLICE)
    if not state or state["status"] != "active":
        raise ValueError("There is no active outbreak response to execute.")
    if not isinstance(ops, (list, tuple)) or not ops or len(ops) > 3 or any(not isinstance(op, str) or op not in ALLOWED for op in ops) or len(set(ops)) != len(ops):
        raise ValueError("During the outbreak choose isolation, surge care, protected food production, or explicitly hold.")
    if "hold" in ops and len(ops) > 1:
        raise ValueError("Holding the current course cannot be mixed with a new outbreak intervention.")
    if "isolate" in ops and "protect_food" in ops:
        raise ValueError("Full isolation and keeping normal growing-deck shifts are different policies. Choose which risk to take.")
    alive = sum(bool(p["alive"]) for p in _people(c).values())
    if not alive:
        raise ValueError("No living crew remain to execute this response.")
    resources = c.world.state["resources"]
    setup = alive * MEDICINE_SETUP_PER_PERSON if "surge_care" in ops else 0.
    if setup and resources["medicine_kg"] < setup + MEDICINE_PER_CARE_DAY:
        raise ValueError("There is not enough medicine to establish a surge-care rotation. Isolation remains available if food reserves permit.")
    food_max = alive * FOOD_KG_PER_PERSON_DAY * WINDOW_DAYS * _food_rate(ops)
    if food_max and resources["food_kg"] - food_max < alive * FOOD_KG_PER_PERSON_DAY * 45:
        raise ValueError("This outbreak policy would spend the protected 45-day food buffer. Keep growing shifts open, use care alone, or explicitly hold.")
    return {"operations": list(ops), "days": WINDOW_DAYS, "medicine_setup_kg": setup, "food_cost_max_kg": food_max}


def _cooperation(c, reactions):
    alive = {pid for pid, person in _people(c).items() if person["alive"]}
    if not isinstance(reactions, dict) or not alive.issubset(reactions):
        raise ValueError("A complete Jev response is required before the outbreak can advance. Nothing changed.")
    weights = {}
    for pid in sorted(alive, key=int):
        probabilities = reactions[pid].get("probabilities", {}) if isinstance(reactions[pid], dict) else {}
        if not isinstance(probabilities, dict) or set(probabilities) != {"support", "question", "oppose"}:
            raise ValueError("Jev returned an incomplete cooperation distribution. Nothing changed.")
        values = list(probabilities.values())
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values) or sum(values) <= 0:
            raise ValueError("Jev returned an invalid cooperation distribution. Nothing changed.")
        total = sum(values)
        weights[pid] = (probabilities["support"] + .45 * probabilities["question"] + .10 * probabilities["oppose"]) / total
    mean = sum(weights.values()) / len(weights)
    return weights, {"mean": mean, "people": len(weights), "mapping": {"support": 1., "question": .45, "oppose": .10}, "source": "Jev full probability distributions"}


# === Actual roster transitions and bounded response window ===
def _die(c, pid, person, case, day):
    if not person["alive"]:
        return False
    people = _people(c)
    partner = people.get(str(person.get("partner")))
    if partner is not None:
        partner["partner"] = None
    person.update(alive=False, partner=None, cause_of_death="bridge_outbreak", died_year=c.world.tick, died_incident_day=day)
    case.update(status="dead", day_resolved=day)
    pop = c.world.state["population"]
    pop["deaths_total"] += 1
    pop["deaths_year"] += 1
    return True


def _sync_population(c):
    pop = c.world.state["population"]
    generations = {}
    for person in pop["people"].values():
        if person["alive"]:
            generation = str(person["generation"])
            generations[generation] = generations.get(generation, 0) + 1
    pop["alive"] = sum(generations.values())
    pop["by_generation"] = generations
    if not pop["alive"]:
        c.status = "lost"


def apply_response(c, ops, reactions):
    """Advance one real policy window, returning daily auditable public frames.

    The caller owns work costs, social effects, the incident lock, save and revision.
    This function owns all outbreak food/medicine costs: do not charge them twice.
    All validation, including complete probabilities, finishes before any mutation.
    """
    validated = validate(c, ops)
    weights, cooperation = _cooperation(c, reactions)
    before = summary(c)
    state = c.world.state[SLICE]
    resources = c.world.state["resources"]
    people = _people(c)
    rng = c.world.rng.fork(RNG_FORK)
    start_day = state["day"]
    initial_alive = before["population"]
    state["last_policy"] = list(ops)
    state["compliance"] = cooperation
    state["last_response_days"] = WINDOW_DAYS
    capacity = math.floor(initial_alive * .08 * (.35 + .65 * cooperation["mean"])) if "surge_care" in ops else 0
    state["care_capacity"] = max(1, capacity) if "surge_care" in ops else 0
    setup = validated["medicine_setup_kg"]
    resources["medicine_kg"] -= setup
    state["medicine_spent_kg"] += setup
    casualties, recovered, frames = [], [], []

    for _ in range(WINDOW_DAYS):
        state["day"] += 1
        day = state["day"]
        live_ids = [pid for pid in state["cohort"] if people[pid]["alive"]]
        active = [pid for pid in live_ids if state["cases"].get(pid, {}).get("status") == "infected"]
        # Staff triage the existing critically ill first. Supplies, not prose or
        # a favorable sentiment label, cap actual treatment each day.
        priority = sorted(active, key=lambda pid: (not state["cases"][pid]["severe"], state["cases"][pid]["day_infected"], int(pid)))
        medicine_capacity = math.floor((resources["medicine_kg"] + 1e-9) / MEDICINE_PER_CARE_DAY)
        treated = set(priority[:max(0, min(state["care_capacity"], medicine_capacity))])
        medicine = len(treated) * MEDICINE_PER_CARE_DAY
        resources["medicine_kg"] = max(0., resources["medicine_kg"] - medicine)
        state["medicine_spent_kg"] += medicine
        state["care_delivered"] = len(treated)
        food = len(live_ids) * FOOD_KG_PER_PERSON_DAY * _food_rate(ops)
        resources["food_kg"] = max(0., resources["food_kg"] - food)
        state["food_spent_kg"] += food
        new_infections, new_deaths, new_recoveries = [], [], []

        prevalence = len(active) / max(1, len(live_ids))
        for pid in live_ids:
            if pid in state["cases"]:
                continue  # recovered people stay immune for this single scenario
            if "isolate" in ops:
                # Both individual and communal execution matter. Do not collapse
                # the distribution to its top class: equal labels can act differently.
                execution = .5 * weights[pid] + .5 * cooperation["mean"]
                contact = .025 + .20 * (1 - execution)
            else:
                contact = 1.15 if "protect_food" in ops else 1.
            probability = 1 - math.exp(-.24 * prevalence * contact)
            if rng.random() < probability:
                state["cases"][pid] = _new_case(rng, day)
                new_infections.append(pid)

        for pid in active:
            case = state["cases"][pid]
            elapsed = day - case["day_infected"]
            critical = case["severe"] and elapsed >= 4
            hazard = .040 if critical else .003
            if pid in treated:
                hazard *= .16
            death_roll = rng.random()
            if death_roll < hazard:
                if _die(c, pid, people[pid], case, day):
                    new_deaths.append(pid)
            elif elapsed >= case["duration"]:
                case.update(status="recovered", day_resolved=day)
                new_recoveries.append(pid)
        casualties.extend(new_deaths)
        recovered.extend(new_recoveries)
        _sync_population(c)
        infected_remaining = any(case["status"] == "infected" and people[pid]["alive"] for pid, case in state["cases"].items())
        state["status"] = "extinct" if c.world.state["population"]["alive"] == 0 else "active" if infected_remaining else "contained"
        frames.append({"day": day, "summary": summary(c), "health": health(c), "population": c.world.state["population"]["alive"], "new_infections": len(new_infections), "new_recoveries": len(new_recoveries), "new_deaths": len(new_deaths), "medicine_spent_kg": round(medicine + (setup if day == start_day + 1 else 0), 2), "food_spent_kg": round(food, 2), "care_delivered": len(treated)})

    # Isolation's morale cost is separate from Jev's existing ±5 social effect.
    if "isolate" in ops:
        morale = c.world.state.get("morale", {})
        morale["aggregate"] = max(0., morale.get("aggregate", 0.) - 7.)
    after = summary(c)
    return {"before": before, "after": after, "frames": frames, "casualties": casualties, "recovered": recovered, "medicine_spent_kg": round(after["medicine_spent_kg"] - before["medicine_spent_kg"], 2), "food_spent_kg": round(after["food_spent_kg"] - before["food_spent_kg"], 2), "compliance": cooperation, "resolved": after["resolved"], "days": WINDOW_DAYS, "policy": list(ops)}
