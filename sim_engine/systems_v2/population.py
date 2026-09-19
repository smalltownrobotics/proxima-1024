"""Population system — cohort-of-individuals model under the framework.

Owns:    world.state['population']
Reads:   world.state['mortality']['modifiers'] (composed by mortality system)
         world.state['fertility']['modifiers']
Emits:   'death', 'birth', 'pair_formed' (informational; no effects)

Engine seams (public API for the bridge and other out-of-tick callers):
    apply_death(world, pid, cause, ...)  — the ONLY sanctioned way to kill a
        person outside this system's own tick. Flips the alive flag, releases
        the partner, maintains the death counters and by_generation, and emits
        a provenance 'death' event. Bridge code must never hand-edit the roster.
    recount(world)                       — authoritative alive/by_generation recount.
    trim_founders(world, expected)       — founder-count normalization (age-band
        rounding can seat one surplus founder; the bridge used to fix this by
        editing the slice directly).

Scale note (2026-09-18): dead people stay in the roster forever (lineage and
life-history retrieval need them), so on a 50k-crew millennium run the people
map grows past a million records. Every per-tick loop therefore works from a
single alive-only scan built once per tick. The scan order is dict insertion
order — identical to the order the old full-map loops visited living people —
so RNG draw order, and therefore every trajectory, is unchanged.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402


AGE_BANDS = ["0-4","5-9","10-14","15-19","20-24","25-29","30-34","35-39","40-44",
             "45-49","50-54","55-59","60-64","65-69","70-74","75-79","80+"]
BAND_LO = {b: int(b.split("-")[0].rstrip("+")) for b in AGE_BANDS}


def age_band(age: float) -> str:
    a = int(age)
    if a >= 80:
        return "80+"
    return AGE_BANDS[a // 5]


@dataclasses.dataclass
class Person:
    pid: int
    age: float
    sex: str               # 'f' | 'm'
    generation: int
    parents: tuple[int, int] | None = None
    partner: int | None = None
    children: list[int] = dataclasses.field(default_factory=list)
    alive: bool = True
    cause_of_death: str | None = None
    died_year: int | None = None


class PopulationSystem(BaseSystem):
    name = "population"
    dependencies: list[str] = []
    emits = ["birth", "death", "pair_formed"]
    subscribes: list[str] = []

    def defaults(self, config: dict) -> dict:
        pop_cfg = config["population"]
        n = int(pop_cfg["initial"])
        fem_frac = float(pop_cfg.get("female_fraction", 0.5))
        pyramid = pop_cfg["age_pyramid"]
        # Normalize pyramid
        total = sum(pyramid.values()) or 1.0
        norm = {b: pyramid.get(b, 0.0) / total for b in AGE_BANDS}
        # Distribute counts
        counts: dict[str, int] = {}
        running = 0
        for i, b in enumerate(AGE_BANDS):
            if i == len(AGE_BANDS) - 1:
                counts[b] = n - running
            else:
                counts[b] = round(n * norm[b])
                running += counts[b]
        # Need RNG for sampling exact ages within bands. Snapshot of seed-derived state.
        # We'll use a derived seed-based generator just for init (deterministic).
        import random as _r
        rng = _r.Random(int(config.get("tunables", {}).get("rng_seed", 42)) ^ 0x504F50)
        people: dict[int, dict] = {}
        next_pid = 1
        for band, c in counts.items():
            for _ in range(c):
                lo = BAND_LO[band]
                hi = lo + 4 if band != "80+" else 95
                age = rng.uniform(lo, hi)
                sex = "f" if rng.random() < fem_frac else "m"
                p = Person(pid=next_pid, age=age, sex=sex, generation=0)
                people[next_pid] = dataclasses.asdict(p)
                next_pid += 1
        return {
            "people": people,
            "next_pid": next_pid,
            "alive": sum(1 for p in people.values() if p["alive"]),
            # Slice-owned now: genetics reads this for its generation amplifier,
            # and the bridge previously maintained it by editing the slice.
            "by_generation": {"0": sum(1 for p in people.values() if p["alive"])},
            "births_year": 0,
            "deaths_year": 0,
            "births_total": 0,
            "deaths_total": 0,
            "pairs_formed_year": 0,
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        cfg = world.config.get("tunables", {})
        rng = world.rng.fork(self.name)

        # Reset per-year counters
        slice_["births_year"] = 0
        slice_["deaths_year"] = 0
        slice_["pairs_formed_year"] = 0

        # Single alive-only scan for the whole tick. Dict insertion order matches
        # the old full-map iteration order restricted to living people, which is
        # what keeps every RNG draw below in the exact same sequence as before
        # the scale pass (see module docstring).
        living = [p for p in slice_["people"].values() if p["alive"]]

        # 1. Aging
        for p in living:
            p["age"] += dt_years

        # 2. Mortality — uses composed modifier from world.state['mortality']
        h0 = float(cfg.get("mortality_h0", 7.0e-5))
        alpha = float(cfg.get("mortality_alpha", 0.0855))
        accident = float(cfg.get("accident_hazard_per_year", 0.0004))
        mort_mod = self._read_modifier(world, "mortality")
        deaths = 0
        for p in living:
            h = h0 * math.exp(alpha * p["age"]) + accident
            p_death = (1.0 - math.exp(-h * dt_years)) * mort_mod
            if rng.random() < p_death:
                p["alive"] = False
                p["cause_of_death"] = "natural" if rng.random() < 0.88 else "accident"
                p["died_year"] = world.tick
                if p["partner"] is not None and p["partner"] in slice_["people"]:
                    slice_["people"][p["partner"]]["partner"] = None
                p["partner"] = None
                deaths += 1
                world.events.emit(Event(
                    kind="death", source=self.name, tick=world.tick,
                    payload={"pid": p["pid"], "age": p["age"], "cause": p["cause_of_death"]},
                ))
        slice_["deaths_year"] = deaths
        slice_["deaths_total"] += deaths

        # 3. Pair formation — 'living' still holds this tick's dead; re-check the flag.
        age_min = float(cfg.get("pair_age_min", 18))
        age_max = float(cfg.get("pair_age_max", 42))
        pair_chance = float(cfg.get("pair_chance_per_year", 0.10)) * dt_years
        eligibles = [
            p for p in living
            if p["alive"] and p["partner"] is None and age_min <= p["age"] <= age_max
        ]
        rng.shuffle(eligibles)
        females = [p for p in eligibles if p["sex"] == "f"]
        males = [p for p in eligibles if p["sex"] == "m"]
        n_pair = min(len(females), len(males))
        for i in range(n_pair):
            if rng.random() < pair_chance * 2:
                f, m = females[i], males[i]
                f["partner"] = m["pid"]
                m["partner"] = f["pid"]
                slice_["pairs_formed_year"] += 1

        # 4. Births
        birth_chance = float(cfg.get("birth_chance_per_year", 0.18)) * dt_years
        max_children = int(cfg.get("max_children_per_pair", 3))
        pop_cap = int(cfg.get("population_ceiling", 9999999))
        alive_count = len(living) - deaths
        newborns: list[dict] = []
        fert_mod = self._read_modifier(world, "fertility")
        if alive_count < pop_cap:
            seen_partner_ids: set[int] = set()
            births = 0
            for f in living:
                if not f["alive"] or f["sex"] != "f" or f["partner"] is None:
                    continue
                if not (age_min <= f["age"] <= age_max):
                    continue
                if len(f["children"]) >= max_children:
                    continue
                if f["pid"] in seen_partner_ids:
                    continue
                m = slice_["people"].get(f["partner"])
                if m is None or not m["alive"]:
                    continue
                seen_partner_ids.add(m["pid"])
                if rng.random() < birth_chance * fert_mod:
                    sex = "f" if rng.random() < 0.49 else "m"
                    child_gen = max(f["generation"], m["generation"]) + 1
                    new_pid = slice_["next_pid"]
                    slice_["next_pid"] += 1
                    child = dataclasses.asdict(Person(
                        pid=new_pid, age=0.0, sex=sex, generation=child_gen,
                        parents=(f["pid"], m["pid"]),
                    ))
                    slice_["people"][new_pid] = child
                    newborns.append(child)
                    f["children"].append(new_pid)
                    m["children"].append(new_pid)
                    births += 1
                    world.events.emit(Event(
                        kind="birth", source=self.name, tick=world.tick,
                        payload={"pid": new_pid, "parents": [f["pid"], m["pid"]], "generation": child_gen},
                    ))
                    if alive_count + births >= pop_cap:
                        break
            slice_["births_year"] = births
            slice_["births_total"] += births

        # Authoritative alive counter + by_generation, maintained from this tick's
        # survivors and newborns without another full-map scan. Genetics (which
        # ticks after population) reads by_generation the same tick it changes.
        gens: dict[str, int] = {}
        alive_now = 0
        for p in living:
            if p["alive"]:
                alive_now += 1
                key = str(p["generation"])
                gens[key] = gens.get(key, 0) + 1
        for p in newborns:
            alive_now += 1
            key = str(p["generation"])
            gens[key] = gens.get(key, 0) + 1
        slice_["alive"] = alive_now
        slice_["by_generation"] = gens

    def _read_modifier(self, world: Any, axis: str) -> float:
        """Read the multiplicative product of modifier lanes from world.state[axis]['modifiers']."""
        slice_ = world.state.get(axis, {})
        mods = slice_.get("modifiers", {})
        product = 1.0
        for v in mods.values():
            try:
                product *= float(v)
            except (TypeError, ValueError):
                continue
        return product

    def emit_snapshot(self, world: Any) -> dict:
        # One alive-only pass; on millennium-scale runs the people map is
        # dominated by the dead and this runs every tick.
        slice_ = world.state[self.name]
        alive = [p for p in slice_["people"].values() if p["alive"]]
        if not alive:
            return {
                "alive": 0, "deaths_year": slice_["deaths_year"],
                "births_year": slice_["births_year"],
                "deaths_total": slice_["deaths_total"], "births_total": slice_["births_total"],
                "median_age": None, "age_pyramid": {b: 0 for b in AGE_BANDS},
                "by_sex": {"f": 0, "m": 0}, "by_generation": {}, "pairs_active": 0,
            }
        ages = sorted(p["age"] for p in alive)
        median = ages[len(ages) // 2]
        pyramid = {b: 0 for b in AGE_BANDS}
        for p in alive:
            pyramid[age_band(p["age"])] += 1
        by_sex = {
            "f": sum(1 for p in alive if p["sex"] == "f"),
            "m": sum(1 for p in alive if p["sex"] == "m"),
        }
        gens: dict[int, int] = {}
        for p in alive:
            gens[p["generation"]] = gens.get(p["generation"], 0) + 1
        pairs = sum(1 for p in alive if p["partner"] is not None) // 2
        return {
            "alive": len(alive),
            "deaths_year": slice_["deaths_year"],
            "births_year": slice_["births_year"],
            "deaths_total": slice_["deaths_total"],
            "births_total": slice_["births_total"],
            "median_age": round(median, 2),
            "age_pyramid": pyramid,
            "by_sex": by_sex,
            "by_generation": {str(k): v for k, v in sorted(gens.items())},
            "pairs_active": pairs,
            "pairs_formed_year": slice_["pairs_formed_year"],
        }


# === Engine seams — public population API for out-of-tick callers ===
# The bridge (outbreak casualties, founder normalization, roster recounts) used
# to reach into world.state['population'] and hand-edit it. These functions are
# now the single sanctioned door: every population change stays engine-owned,
# counter-consistent, and event-audited. RNG is deliberately NOT drawn here —
# callers decide who dies and why (from their own persisted fork); this seam
# owns the bookkeeping so it can never drift from the annual systems.

def apply_death(world: Any, pid: int | str, cause: str,
                incident_day: int | None = None) -> bool:
    """Kill one living person, engine-side. Returns False if already dead/unknown.

    Flips the alive flag once, releases the partner both ways, stamps
    cause/died_year (and died_incident_day for tactical-clock deaths),
    increments deaths_year/deaths_total, maintains alive/by_generation, and
    emits a provenance 'death' event on the bus (informational, no effects;
    it is dispatched with the caller's next dispatch/step).
    """
    slice_ = world.state["population"]
    people = slice_["people"]
    person = people.get(pid) or people.get(int(pid) if str(pid).isdigit() else pid)
    if person is None or not person["alive"]:
        return False
    partner = people.get(person.get("partner"))
    if partner is not None:
        partner["partner"] = None
    person["alive"] = False
    person["partner"] = None
    person["cause_of_death"] = cause
    person["died_year"] = world.tick
    if incident_day is not None:
        person["died_incident_day"] = incident_day
    slice_["deaths_year"] += 1
    slice_["deaths_total"] += 1
    slice_["alive"] -= 1
    gens = slice_.get("by_generation") or {}
    key = str(person["generation"])
    if key in gens:
        gens[key] -= 1
        if gens[key] <= 0:
            del gens[key]
    slice_["by_generation"] = gens
    world.events.emit(Event(
        kind="death", source="population", tick=world.tick,
        payload={"pid": person["pid"], "age": person["age"], "cause": cause,
                 **({"incident_day": incident_day} if incident_day is not None else {})},
    ))
    return True


def recount(world: Any) -> dict:
    """Authoritative full recount of alive + by_generation into the slice.

    Normally unnecessary — tick() and apply_death() keep the counters
    incremental — but this is the safety net after loading legacy saves or any
    bulk roster surgery. Returns {'alive': int, 'by_generation': dict}.
    """
    slice_ = world.state["population"]
    gens: dict[str, int] = {}
    for person in slice_["people"].values():
        if person["alive"]:
            key = str(person["generation"])
            gens[key] = gens.get(key, 0) + 1
    slice_["alive"] = sum(gens.values())
    slice_["by_generation"] = gens
    return {"alive": slice_["alive"], "by_generation": gens}


def trim_founders(world: Any, expected: int) -> int:
    """Drop surplus founders created by age-band rounding; returns count removed.

    Only valid before the first tick (no pairings, children, or dependent
    state exist yet). Highest-pid founders are removed so the seat order the
    RNG produced for the first `expected` people is untouched.
    """
    slice_ = world.state["population"]
    surplus = sorted(slice_["people"])[expected:]
    for pid in surplus:
        del slice_["people"][pid]
    recount(world)
    return len(surplus)
