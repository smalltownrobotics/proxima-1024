"""ResourcesSystem — food/water/oxygen/medicine ledger under v2 framework.

Owns:    world.state['resources']
Reads:   world.state['population']['alive']
Writes (own slice only): food_kg, water_kg, oxygen_kg, medicine_kg, *_adequacy
Emits Effects: into mortality.modifiers.food, fertility.modifiers.food, etc.

Mass balance: production via hydroponics × morale-modulated yield - consumption per
person per year. Closed-loop water/oxygen recovery efficiency drives net loss.
Adequacy ratios feed into mortality + fertility modifier lanes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402


class ResourcesSystem(BaseSystem):
    name = "resources"
    dependencies = ["population", "morale"]
    emits = ["resource_event"]

    def defaults(self, config: dict) -> dict:
        ship = config.get("ship", {})
        crew = int(config.get("population", {}).get("initial", 100))
        days_buffer = 180
        return {
            "food_kg": float(ship.get("food_storage_kg", crew * 1.2 * days_buffer)),
            "water_kg": float(ship.get("water_storage_kg", crew * 4.0 * days_buffer)),
            "oxygen_kg": float(ship.get("oxygen_storage_kg", crew * 0.84 * days_buffer)),
            "medicine_kg": float(ship.get("medicine_storage_kg", crew * 0.5 * 1.5)),
            "food_adequacy": 1.0,
            "water_adequacy": 1.0,
            "medicine_adequacy": 1.0,
            "agricultural_area_m2": float(ship.get("agricultural_area_m2", crew * 20)),
            "yield_per_m2": float(ship.get("food_yield_per_m2_per_year", 28.0)),
            "water_recovery_efficiency": float(ship.get("water_recovery_efficiency", 0.93)),
            "oxygen_recovery_efficiency": float(ship.get("oxygen_recovery_efficiency", 0.93)),
            "reactor_efficiency": float(ship.get("reactor_efficiency", 1.0)),
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        alive = max(1, world.read("population.alive") or 1)
        morale = world.read("morale.aggregate") or 70.0

        # Demand
        food_demand = alive * 438.0 * dt_years    # 1.2 kg/day
        water_demand = alive * 1460.0 * dt_years
        medicine_demand = alive * 0.5 * dt_years

        # Production
        morale_factor = max(0.5, min(1.05, morale / 80.0))
        food_produced = (slice_["agricultural_area_m2"] * slice_["yield_per_m2"]
                         * morale_factor * slice_["reactor_efficiency"] * dt_years)

        # Apply
        slice_["food_kg"] += (food_produced - food_demand)
        slice_["water_kg"] -= water_demand * (1.0 - slice_["water_recovery_efficiency"])
        slice_["oxygen_kg"] -= alive * 365 * 0.84 * (1.0 - slice_["oxygen_recovery_efficiency"]) * dt_years
        slice_["medicine_kg"] = max(0.0, slice_["medicine_kg"] - medicine_demand)

        # Adequacy
        if slice_["food_kg"] >= 0:
            slice_["food_adequacy"] = 1.0
        else:
            slice_["food_adequacy"] = max(0.3, 1.0 - abs(slice_["food_kg"]) / max(food_demand, 1.0))
            slice_["food_kg"] = 0.0

        if slice_["water_kg"] < 0:
            slice_["water_kg"] = 0.0
            slice_["water_adequacy"] = slice_["water_recovery_efficiency"] if slice_["water_recovery_efficiency"] < 0.99 else 1.0
        else:
            slice_["water_adequacy"] = 1.0

        slice_["medicine_adequacy"] = (
            1.0 if slice_["medicine_kg"] > medicine_demand * 0.5
            else max(0.3, slice_["medicine_kg"] / max(medicine_demand, 1.0))
        )

        # Emit modifier-lane updates via Effects (the bus applies them at end of tick)
        food_ad = slice_["food_adequacy"]
        water_ad = slice_["water_adequacy"]
        med_ad = slice_["medicine_adequacy"]

        # Mortality lane: lower adequacy → higher mortality
        mort_mult = 1.0
        if food_ad < 1.0:
            mort_mult *= 1.0 + (1.0 - food_ad) * 1.5
        if water_ad < 1.0:
            mort_mult *= 1.0 + (1.0 - water_ad) * 0.8
        if med_ad < 0.7:
            mort_mult *= 1.0 + (0.7 - med_ad) * 0.6
        world.events.emit(Event(
            kind="resource_event", source=self.name, tick=world.tick,
            effects=(Effect(path="mortality.modifiers.resources", op="set", value=round(mort_mult, 3)),),
        ))

        # Fertility lane: low food → low fertility
        fert_mult = 1.0
        if food_ad < 1.0:
            fert_mult *= food_ad ** 1.5
        world.events.emit(Event(
            kind="resource_event", source=self.name, tick=world.tick,
            effects=(Effect(path="fertility.modifiers.resources", op="set", value=round(fert_mult, 3)),),
        ))

    def emit_snapshot(self, world: Any) -> dict:
        s = world.state[self.name]
        return {
            "food_kg": round(s["food_kg"], 0),
            "water_kg": round(s["water_kg"], 0),
            "oxygen_kg": round(s["oxygen_kg"], 0),
            "medicine_kg": round(s["medicine_kg"], 1),
            "food_adequacy": round(s["food_adequacy"], 3),
            "water_adequacy": round(s["water_adequacy"], 3),
            "medicine_adequacy": round(s["medicine_adequacy"], 3),
        }
