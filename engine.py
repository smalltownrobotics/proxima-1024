"""Proxima Bridge: journey generation, incremental v2 worlds, and player orders.

Owns fictional campaign state. The original engine stays unchanged. All decisions
have explicit mechanical effects; generated prose never silently edits resources.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import random
import secrets
import sys
import time

import yaml
import outbreak

ROOT = Path(__file__).resolve().parent
SIM_ENGINE = ROOT / "sim_engine"
sys.path.insert(0, str(SIM_ENGINE))
from run_v2 import build_registry
from framework import build_world, Orchestrator, Event, Effect
import life_history
from systems_v2.population import apply_death as _pop_apply_death, recount as _pop_recount, trim_founders as _pop_trim_founders

# === Catalog and ship diagram contract ===
DATA = ROOT / "data"
ROOMS = [
    {"id": "bridge", "name": "Command", "code": "A1", "role": "command and navigation", "priority": "mission continuity", "x": 320, "y": 36, "w": 240, "h": 90},
    {"id": "habitat", "name": "Living quarters", "code": "B1", "role": "habitat residents and teachers", "priority": "family wellbeing and a fair say", "x": 114, "y": 166, "w": 300, "h": 160},
    {"id": "gardens", "name": "Hydroponics", "code": "B2", "role": "growers and life-support crew", "priority": "ecosystem stability and sustainable workload", "x": 466, "y": 166, "w": 300, "h": 160},
    {"id": "medical", "name": "Medical / cryo", "code": "C1", "role": "medical crew and dependents", "priority": "health, consent, and emergency readiness", "x": 114, "y": 366, "w": 300, "h": 140},
    {"id": "commons", "name": "Commons / archive", "code": "C2", "role": "researchers and civic representatives", "priority": "discovery, shared knowledge, and accountability", "x": 466, "y": 366, "w": 300, "h": 140},
    {"id": "drive", "name": "Engineering", "code": "D1", "role": "engineers and maintenance crew", "priority": "safe machinery and workable orders", "x": 280, "y": 546, "w": 320, "h": 126},
]

# Names and specialties are explicit fictional character dressing. Age, health,
# generation and survival still come from the actual simulated roster.
PROFILE_GIVEN = ("Amara", "Rowan", "Inez", "Samir", "Mara", "Theo", "Nadia", "Leon", "Anika", "Elias", "June", "Ravi", "Mina", "Owen", "Sora", "Ada", "Idris", "Noor", "Talia", "Kai", "Rhea", "Emil", "Lina", "Arun", "Esme", "Nico", "Zara", "Remy", "Mika", "Luca", "Alma", "Sol")
PROFILE_FAMILY = ("Vega", "Chen", "Okafor", "Singh", "Torres", "Reed", "Kim", "Patel", "Silva", "Haddad", "Park", "Rivera", "Sato", "Marin", "Khan", "Mensah", "Ito", "Costa", "Malik", "Hayes", "Novak", "Rossi", "Lin", "Bennett", "Aziz", "Morgan", "Das", "Dubois", "Aoki", "Ward", "Nair", "Bell")
PROFILE_SPECIALTIES = {
    "bridge": ("Mission planning", "Navigation watch", "Command continuity", "Operations coordination"),
    "habitat": ("Community care", "Education", "Habitat stewardship", "Family support"),
    "gardens": ("Hydroponics", "Crop resilience", "Food distribution", "Closed-loop ecology"),
    "medical": ("Emergency care", "Infection control", "Medical stores", "Cryogenic care"),
    "commons": ("Civic mediation", "Shared archives", "Public accountability", "Research coordination"),
    "drive": ("Thermal systems", "Propulsion maintenance", "Life-support engineering", "Fabrication"),
}

SITUATIONS = {
    "departure": {"title": "What kind of ship will we become?", "room": "bridge", "description": "The departure council must decide where the first year of shared effort goes. Every choice begins a different institutional history.", "actions": ["charter", "gardens", "maintenance"]},
    "food": {"title": "The margin between harvests", "room": "gardens", "description": "Review the food reserve and growing capacity. More production needs space and labor; consuming the buffer makes today easier and tomorrow less forgiving.", "actions": ["gardens", "ration", "hold"]},
    "repair": {"title": "Time, or the machinery?", "room": "drive", "description": "Engineering asks how aggressively to maintain the ship. Protecting the hardware can mean more work, less reserve, or a later arrival.", "actions": ["maintenance", "slow", "hold"]},
    "trust": {"title": "Who gets to decide?", "room": "commons", "description": "The council reviews how authority should work aboard. The crew will respond to this order in light of what earlier orders actually cost them.", "actions": ["charter", "emergency", "hold"]},
    "health": {"title": "Care for the people carrying us", "room": "medical", "description": "Medical asks for time and resources to replenish supplies. The ship cannot spend the same reserve on everything.", "actions": ["medical", "rest", "hold"]},
}
ACTIONS = {
    "charter": {"title": "Give the crew a vote", "detail": "Publish the plan and hold an advisory vote within the existing government.", "tradeoff": "Morale +6 · mandate +6 · food reserve −3%", "effects": [("morale.aggregate", "add", 6), ("governance.legitimacy", "add", .06), ("resources.food_kg", "mul", .97)]},
    "gardens": {"title": "Expand the growing decks", "detail": "Convert available interior space and assign a construction rotation.", "tradeoff": "Growing area +8% · food reserve −8% · morale −3", "effects": [("resources.agricultural_area_m2", "mul", 1.08), ("resources.food_kg", "mul", .92), ("morale.aggregate", "add", -3)]},
    "maintenance": {"title": "Fund a maintenance rotation", "detail": "Commit stores and shared labor to preventive repairs.", "tradeoff": "Integrity +5 · food reserve −6% · morale −2", "effects": [("ship_integrity.integrity", "add", .05), ("resources.food_kg", "mul", .94), ("morale.aggregate", "add", -2)]},
    "ration": {"title": "Protect a reserve", "detail": "Recover avoidable food losses and impose stricter distribution.", "tradeoff": "Production efficiency +4% · morale −6", "effects": [("resources.yield_per_m2", "mul", 1.04), ("morale.aggregate", "add", -6)]},
    "slow": {"title": "Take the slower, safer course", "detail": "Allow two extra years for lower-stress operation and repairs.", "tradeoff": "Journey +2 years · integrity +8 · morale −3", "effects": [("ship_integrity.voyage_extension_years", "add", 2), ("ship_integrity.integrity", "add", .08), ("morale.aggregate", "add", -3)]},
    "emergency": {"title": "Invoke emergency authority", "detail": "Centralize the schedule and push critical maintenance through.", "tradeoff": "Integrity +3 · mandate −8 · morale −4", "effects": [("ship_integrity.integrity", "add", .03), ("governance.legitimacy", "add", -.08), ("morale.aggregate", "add", -4)]},
    "medical": {"title": "Replenish medical stores", "detail": "Use the fabrication and synthesis allowance for medicine.", "tradeoff": "+1 year of medicine · food reserve −7%", "effects": [("resources.food_kg", "mul", .93)]},
    "rest": {"title": "Give the ship a recovery year", "detail": "Reduce optional duties and prioritize rest.", "tradeoff": "Morale +8 · integrity −2", "effects": [("morale.aggregate", "add", 8), ("ship_integrity.integrity", "add", -.02)]},
    "hold": {"title": "Stay the course", "detail": "Keep current allocations and accept the existing risks.", "tradeoff": "No immediate resource change", "effects": []},
}


def catalog():
    def read(name, key):
        return yaml.safe_load((DATA / name).read_text())[key]
    destinations = [d for d in read("exoplanets.yaml", "exoplanets") if d["id"] in {"proxima_b", "tau_ceti_e", "trappist_1_e", "ross_128_b"}]
    drives = [p for p in read("propulsion.yaml", "propulsion") if p["id"] in {"prop_fusion_pulse_daedalus", "prop_fusion_continuous", "prop_antimatter_catalyzed"}]
    ships = [s for s in read("ships.yaml", "ships") if s["id"] in {"ship_modular_cluster", "ship_stanford_torus", "ship_aurora_ark"}]
    tech = {t["id"]: t for t in read("tech_timeline.yaml", "technologies")}
    for d in drives:
        t = tech[d["id"]]
        d.update(name=t["name"], earliest_year=t["likely_year"])
    return {"destinations": destinations, "drives": drives, "ships": ships, "rooms": ROOMS}


def make_config(options, variant=0):
    scenario = options.get("scenario")
    if scenario is not None and scenario not in ("normal", "outbreak"):
        raise ValueError("Choose a supported voyage scenario: normal or outbreak.")
    c = catalog()
    def choose(group, key):
        return next((x for x in c[group] if x["id"] == options.get(key)), c[group][0])
    d, drive, ship = choose("destinations", "destination"), choose("drives", "drive"), choose("ships", "ship")
    if drive["id"] not in ship["compatible_propulsion"]:
        raise ValueError("That propulsion system cannot support this ship class.")
    n = int(options.get("crew", 1024))
    # Founding crews range from the architecture minimum to 50,000. Catalog
    # max_crew describes a nominal configuration, not a hard manifest cap;
    # hull capacity values scale with n below.
    if not max(200, ship["min_crew"]) <= n <= 50000:
        raise ValueError("Choose a founding crew between the ship minimum and 50,000.")
    velocity = float(options.get("speed", .05))
    if not .02 <= velocity <= min(.15, drive["max_velocity_c"]):
        raise ValueError("Cruise speed exceeds the selected drive's modeled envelope.")
    year = int(options.get("launch_year", 2200))
    if not drive["earliest_year"] <= year <= 2500:
        raise ValueError("Launch year is earlier than this drive's speculative availability.")
    years = math.ceil(d["distance_ly"] / velocity)
    if years > 1500:
        raise ValueError("This prototype supports journeys of up to 1,500 cruise years.")
    cfg = yaml.safe_load((SIM_ENGINE / "configs/mission_control.yaml").read_text())
    cfg["name"] = ["Wayfarer", "Meridian", "Longview", "Peregrine", "Kindred", "Aster"][variant % 6]
    cfg["mission"] = {"destination": d["id"], "destination_name": d["name"], "distance_ly": d["distance_ly"], "velocity_c": velocity, "voyage_years": years, "launch_year": year, "propulsion": drive["id"], "propulsion_name": drive["name"]}
    cfg["population"]["initial"] = n
    cfg["population"]["ethos"] = options.get("ethos", "mission_scientists") if options.get("ethos") in {"mission_scientists", "mixed_voluntary", "religious_refugees"} else "mission_scientists"
    cfg["ship"].update({"class": ship["id"], "class_name": ship["name"], "density_tons_per_person": ship["density_tons_per_person"], "habitable_volume_m3": n * 60, "agricultural_area_m2": n * (21 + variant * .6), "food_storage_kg": n * 1.2 * (180 + 12 * variant), "water_recovery_efficiency": .975, "oxygen_recovery_efficiency": .98, "medicine_storage_kg": n * 4})
    # V2 manifest controls have actual engine consequences, not decorative values.
    reserve = int(options.get("reserve_days", 240))
    medical = int(options.get("medicine_years", 8))
    if not 120 <= reserve <= 360 or not 4 <= medical <= 12:
        raise ValueError("Manifest reserves are outside this ship's supported range.")
    cfg["ship"]["food_storage_kg"] = n * 1.2 * (reserve + 6 * variant)
    cfg["ship"]["medicine_storage_kg"] = n * .5 * medical
    cfg["policy"].update({"cryo": "none", "governance": "technocracy"})
    cfg["tunables"].update({"rng_seed": secrets.randbits(32), "population_ceiling": n * 2})
    # Commissioning still uses the unmodified annual engine. This merely arms an
    # explicit live-voyage challenge; the crisis is not injected into trial runs.
    cfg.pop("scenario", None)
    if scenario == "outbreak":
        cfg["scenario"] = {"kind": "outbreak", "onset_year": 4, "version": 1}
    return cfg


def world_for(config):
    world = build_world(copy.deepcopy(config))
    reg = build_registry()
    orch = Orchestrator(reg, world, dt_years=1.0)
    orch.initialize()
    # Age-band rounding can seat one surplus founder; the engine's own seam
    # normalizes the roster before any pairings or dependent simulation ticks.
    _pop_trim_founders(world, config["population"]["initial"])
    return world, reg, orch


# === A: engine seams — population authority lives in the engine (2026-09-18) ===
# Bridge code must not hand-edit world.state['population'] (or any slice it
# does not own). These are the sanctioned doors; see the population module
# for the full counter, partnership, and death-provenance contract.

def kill_person(world, pid, cause, incident_day=None):
    """Engine-owned roster death: flags, partner release, counters, provenance
    event. Returns False if the person is already dead or unknown. Replaces
    direct roster edits (e.g., outbreak casualty bookkeeping)."""
    return _pop_apply_death(world, pid, cause, incident_day=incident_day)


def recount_population(world):
    """Authoritative alive/by_generation recount into the population slice."""
    return _pop_recount(world)


def refresh_generations(world):
    # Kept for existing callers (voyage.travel_tick); the engine seam is the
    # authority now. Since 2026-09-18 the population system maintains alive and
    # by_generation itself every tick, so this is a cheap idempotent safety net.
    _pop_recount(world)


def apply_effects(world, effects, kind="bridge_adjustment", source="bridge", payload=None):
    """Route bridge-side numeric changes through the engine's event bus.

    `effects` is an iterable of framework Effect objects (or (path, op, value)
    tuples). Emitting + dispatching here means every mutation carries event
    provenance instead of being a silent slice write. Prefer this over direct
    dict edits for resource/morale costs computed outside a captain order.
    """
    resolved = tuple(e if isinstance(e, Effect) else Effect(path=e[0], op=e[1], value=e[2]) for e in effects)
    world.events.emit(Event(kind=kind, source=source, tick=world.tick, payload=payload or {}, effects=resolved))
    world.events.dispatch_pending(world)


def generate(options):
    """Six independent eight-year trials, uniform selection, fresh live randomness."""
    configs, candidates = [], []
    for i in range(6):
        cfg = make_config(options, i)
        w, _, o = world_for(cfg)
        for _ in range(8):
            o.step()
            refresh_generations(w)
        configs.append(cfg)
        candidates.append({"name": cfg["name"], "crew": w.read("population.alive"), "integrity": round(w.read("ship_integrity.integrity") * 100), "morale": round(w.read("morale.aggregate")), "growing_area": cfg["ship"]["agricultural_area_m2"], "years": 8, "trial_config": copy.deepcopy(cfg)})
    selected = secrets.randbelow(6)
    cfg = configs[selected]
    cfg["tunables"]["rng_seed"] = secrets.randbits(32)
    return Campaign(cfg, candidates, selected)


def rng_dump(rng):
    return {"seed": rng.seed, "state": rng._rng.getstate(), "forks": {k: rng_dump(v) for k, v in rng._forks.items()}}


def rng_restore(rng, data):
    def tuples(v):
        return tuple(tuples(x) for x in v) if isinstance(v, list) else v
    rng._rng.setstate(tuples(data["state"]))
    for key, value in data["forks"].items():
        rng_restore(rng.fork(key), value)


class Campaign:
    def __init__(self, config, candidates=None, selected=0):
        self.id = secrets.token_hex(8)
        self.config = config
        self.world, self.reg, self.orch = world_for(config)
        self.candidates, self.selected = candidates or [], selected
        self.situation = "departure"
        self.focus = "bridge"
        self.history, self.messages, self.snapshots = [], [], []
        self.reactions = {}
        self.jev_runs = []
        self.astra_runs = []
        self.status = "in_flight"
        self.situation_note = ""
        self.play = None
        outbreak.ensure(self)

    def summary(self):
        w = self.world
        alive = max(0, w.read("population.alive"))
        extended = math.ceil(self.config["mission"]["voyage_years"] + w.read("ship_integrity.voyage_extension_years"))
        return {"year": w.tick, "earth_year": int(w.earth_year), "crew": alive, "births": w.read("population.births_total"), "deaths": w.read("population.deaths_total"), "morale": round(w.read("morale.aggregate"), 1), "integrity": round(w.read("ship_integrity.integrity") * 100, 1), "mandate": round(w.read("governance.legitimacy") * 100, 1), "food_days": round(w.read("resources.food_kg") / max(1, alive * 1.2)), "medicine_years": round(w.read("resources.medicine_kg") / max(1, alive * .5), 1), "food_adequacy": w.read("resources.food_adequacy"), "arrival_year": int(extended), "remaining": max(0, int(extended) - w.tick), "active_failure": w.read("ship_integrity.active_failure"), "governance": w.read("governance.type"), "generation": max((int(k) for k in (w.read("population.by_generation") or {})), default=0)}

    def _person_row(self, pid, person, health):
        # Stable social roles, not inferred from sex or age. These are fictional.
        # Keep living and casualty locations identical across refreshes so a death
        # removes an actual person, rather than recoloring an unrelated dot.
        h = int(hashlib.sha256(str(pid).encode()).hexdigest()[:8], 16)
        room = ROOMS[h % len(ROOMS)]
        age = int(person["age"])
        profile = {"name": PROFILE_GIVEN[(h >> 4) % len(PROFILE_GIVEN)] + " " + PROFILE_FAMILY[(h >> 12) % len(PROFILE_FAMILY)], "specialty": PROFILE_SPECIALTIES[room["id"]][(h >> 20) % 4] if age >= 18 else "Community learner", "experience_years": min(max(0, age - 18), 5 + (h >> 8) % 31), "synthetic_profile": True, "profile_source": "Fictional name, specialty and experience; actual simulated age, generation and health."}
        return {"id": str(pid), "room": room["id"], "generation": person["generation"], "age": age, "role": room["role"], "priority": room["priority"], "temperament": ["cautious", "pragmatic", "cooperative", "independent"][h % 4], "response": self.reactions.get(str(pid), {}).get("choice", "unpolled"), "health": health, **profile}

    def crew(self):
        health = outbreak.health(self)
        rows = []
        for pid, p in self.world.state["population"]["people"].items():
            if not p["alive"]:
                continue
            rows.append(self._person_row(pid, p, health.get(str(pid), {}).get("status", "susceptible")))
        return rows

    def casualties(self):
        """Only actual outbreak deaths, retained for the public consequence field."""
        health = outbreak.health(self)
        return [self._person_row(pid, person, "dead")
                for pid, person in self.world.state["population"]["people"].items()
                if not person["alive"] and person.get("cause_of_death") == "bridge_outbreak"
                and health.get(str(pid), {}).get("status") == "dead"]

    def history_book(self):
        """Per-campaign deterministic life-history source (sim/life_history.py).

        Derived from the campaign seed + roster, never saved; rebuilt lazily
        whenever the people map identity changes (fresh campaign or load).
        """
        people = self.world.state["population"]["people"]
        book = getattr(self, "_history_book", None)
        if book is None or book.people is not people:
            book = life_history.LifeHistoryBook(self.config["tunables"]["rng_seed"], people)
            self._history_book = book
        return book

    def person_history(self, pid):
        """Full empirically anchored fictional profile for one roster member:
        Big Five + cognitive ability, career track/grade, promotions,
        discipline, honors, and (for casualties) the recorded death. For
        profile inspection and council voices; read-only, no RNG side effects."""
        return self.history_book().profile(pid, self.world.tick)

    def public(self):
        s = copy.deepcopy(SITUATIONS[self.situation])
        s["id"] = self.situation
        s["note"] = self.situation_note
        s["actions"] = [{"id": key, **{k: v for k, v in ACTIONS[key].items() if k != "effects"}} for key in s["actions"]]
        counts = {k: sum(1 for r in self.reactions.values() if r["choice"] == k) for k in ("support", "question", "oppose")}
        play = copy.deepcopy({k: v for k, v in self.play.items() if k not in {"blocked_runs", "wake_checks", "advisory_audits"}}) if self.play else None
        if play is not None:
            play["wake_checks"] = [{"year": x["year"], "wake_probability": x["wake_probability"]} for x in self.play.get("wake_checks", [])[-24:]]
            play["responses"] = copy.deepcopy(self.reactions)
        p = self.play or {}
        captain = {"role": "You are the admiral", "state": "cryogenesis" if p.get("mode") == "cryo" else "awake", "cycles": p.get("captain_cycles", 0), "awake_days": p.get("captain_awake_days", 0), "slept_years": p.get("captain_slept_years", 0)}
        return {"id": self.id, "name": self.config["name"], "mission": self.config["mission"], "ship": self.config["ship"], "stats": self.summary(), "status": self.status, "crew": self.crew(), "casualties": self.casualties(), "outbreak": outbreak.summary(self), "captain": captain, "situation": s, "focus": self.focus, "history": self.history[-24:], "messages": self.messages[-40:], "candidates": self.candidates, "selected": self.selected, "reactions": counts, "play": play, "jev": {"runs": len(self.jev_runs), "last": self.jev_runs[-1]["summary"] if self.jev_runs else None}, "astra": {"runs": len(self.astra_runs), "model": "gpt-6-astra"}}

    def context(self):
        d = self.public()
        d.pop("crew")
        d.pop("casualties")
        d.pop("messages")
        d.pop("candidates")
        d["schematic"] = [{k: r[k] for k in ("id", "name", "code", "role")} for r in ROOMS]
        d["available_situations"] = {k: {"title": v["title"], "room": v["room"]} for k, v in SITUATIONS.items()}
        return d

    def advance(self, action, years, reactions):
        years = int(years)
        if years not in (1, 3, 5):
            raise ValueError("Choose 1, 3, or 5 years.")
        if self.status != "in_flight":
            raise ValueError("This expedition has ended.")
        if action not in SITUATIONS[self.situation]["actions"]:
            raise ValueError("That order is not available in the current situation.")
        before = self.summary()
        self.reactions = reactions
        a = ACTIONS[action]
        effects = [Effect(path=path, op=op, value=value) for path, op, value in a["effects"]]
        if action == "medical":
            effects.append(Effect(path="resources.medicine_kg", op="add", value=before["crew"] * .5))
        support = sum(r["probabilities"]["support"] for r in reactions.values()) / max(1, len(reactions))
        oppose = sum(r["probabilities"]["oppose"] for r in reactions.values()) / max(1, len(reactions))
        effects.extend([Effect(path="morale.aggregate", op="add", value=(support - oppose) * 5), Effect(path="governance.legitimacy", op="add", value=(support - oppose) * .025)])
        effects.extend([Effect(path="morale.aggregate", op="clamp", value=[0, 100]), Effect(path="governance.legitimacy", op="clamp", value=[0, 1]), Effect(path="ship_integrity.integrity", op="clamp", value=[0, 1])])
        self.world.events.emit(Event(kind="player_order", source="bridge", tick=self.world.tick, payload={"action": action, "support_weight": support, "oppose_weight": oppose}, effects=tuple(effects)))
        self.world.events.dispatch_pending(self.world)
        encountered = []
        for _ in range(years):
            snap = self.orch.step()
            refresh_generations(self.world)
            self.snapshots.append(snap)
            encountered.extend(e["kind"] for e in snap.get("events", []) if e["kind"] not in {"death", "birth", "pair_formed", "resource_event", "modifier_update", "biology_tier_active", "ethos_active", "genetics_pressure_active", "pathogen_pressure_active", "ship_integrity_active"})
            current = self.summary()
            if current["crew"] == 0:
                self.status = "lost"
                break
            if current["remaining"] == 0:
                self.status = "arrived"
                break
        after = self.summary()
        record = {"year": after["year"], "action": action, "title": a["title"], "before": before, "after": after, "events": list(dict.fromkeys(encountered))[:12], "support": round(support, 3)}
        self.history.append(record)
        # A coherent default exists if Astra disconnects after advancing, but is not
        # presented as an Astra decision. The UI reports the failed model call.
        self.situation = "health" if after["medicine_years"] < 2 else "repair" if after["integrity"] < 85 else "food" if after["food_days"] < 90 else "trust"
        self.focus = SITUATIONS[self.situation]["room"]
        self.situation_note = ""
        return record

    def save(self):
        folder = ROOT / "state"
        folder.mkdir(mode=0o700, exist_ok=True)
        data = {"version": 1, "config": self.config, "world": self.world.state, "tick": self.world.tick, "sim_year": self.world.sim_year, "earth_year": self.world.earth_year, "rng": rng_dump(self.world.rng)}
        for k in ("id", "candidates", "selected", "situation", "focus", "history", "messages", "reactions", "jev_runs", "astra_runs", "status", "situation_note"):
            data[k] = getattr(self, k)
        data["play"] = self.play
        target = folder / (self.id + ".json")
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(data, default=str))
        temp.chmod(0o600)
        temp.replace(target)

    @classmethod
    def load(cls, path):
        d = json.loads(path.read_text())
        obj = cls(d["config"])
        obj.world.state = d["world"]
        obj.world.state["population"]["people"] = {int(k): v for k, v in obj.world.state["population"]["people"].items()}
        for k in ("tick", "sim_year", "earth_year"):
            setattr(obj.world, k, d[k])
        rng_restore(obj.world.rng, d["rng"])
        for k in ("id", "candidates", "selected", "situation", "focus", "history", "messages", "reactions", "jev_runs", "astra_runs", "status", "situation_note"):
            setattr(obj, k, d[k])
        obj.play = d.get("play")
        return obj
