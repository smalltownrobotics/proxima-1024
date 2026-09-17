"""Population system — cohort-of-individuals model under the framework.

Owns:    world.state['population']
Reads:   world.state['mortality']['modifiers'] (composed by mortality system)
         world.state['fertility']['modifiers']
Emits:   'death', 'birth', 'pair_formed' (informational; no effects)
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

        # 1. Aging
        for pid, p in slice_["people"].items():
            if p["alive"]:
                p["age"] += dt_years

        # 2. Mortality — uses composed modifier from world.state['mortality']
        h0 = float(cfg.get("mortality_h0", 7.0e-5))
        alpha = float(cfg.get("mortality_alpha", 0.0855))
        accident = float(cfg.get("accident_hazard_per_year", 0.0004))
        mort_mod = self._read_modifier(world, "mortality")
        deaths = 0
        for pid, p in slice_["people"].items():
            if not p["alive"]:
                continue
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
                    payload={"pid": pid, "age": p["age"], "cause": p["cause_of_death"]},
                ))
        slice_["deaths_year"] = deaths
        slice_["deaths_total"] += deaths

        # 3. Pair formation
        age_min = float(cfg.get("pair_age_min", 18))
        age_max = float(cfg.get("pair_age_max", 42))
        pair_chance = float(cfg.get("pair_chance_per_year", 0.10)) * dt_years
        eligibles = [
            p for p in slice_["people"].values()
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
        alive_count = sum(1 for p in slice_["people"].values() if p["alive"])
        fert_mod = self._read_modifier(world, "fertility")
        if alive_count < pop_cap:
            seen_partner_ids: set[int] = set()
            births = 0
            for f in list(slice_["people"].values()):
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

        # Update authoritative alive counter so other systems can read 'population.alive'
        slice_["alive"] = sum(1 for p in slice_["people"].values() if p["alive"])

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
