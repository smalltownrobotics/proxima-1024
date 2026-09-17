"""EthosSystem — applies ethos modifiers to mortality, fertility, morale.

Owns:    world.state['ethos']
Reads:   world.config['population']['ethos']
Emits via Effects: mortality.modifiers.ethos, fertility.modifiers.ethos,
                   morale.aggregate_offset (used by morale stub)

Ethos modifiers are constant for the run (founding population's character is
locked at departure). The system just re-emits them each tick to keep the lanes
populated.

Numbers from research-grounded ETHOS_EFFECTS dict (citations in old sim.py):
- mission_scientists: Antarctic discipline + selection effect
- religious_refugees: Hutterite-tier natalism
- exiled_political_faction: Mariel cohort + IDP mortality
- commercial_expedition: Offshore-rig fatality data
- last_of_earth_survivors: Holocaust offspring elevated mortality
- utopian_commune: Kibbutz longevity
- military_garrison: Active-duty mortality (selection)
- mixed_voluntary: baseline
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402


ETHOS_EFFECTS = {
    "mission_scientists":      {"mortality_mult": 0.94, "fertility_mult": 0.82, "morale_baseline": 4.0},
    "religious_refugees":      {"mortality_mult": 0.93, "fertility_mult": 1.25, "morale_baseline": 7.0},
    "exiled_political_faction":{"mortality_mult": 1.06, "fertility_mult": 0.92, "morale_baseline": -9.0},
    "commercial_expedition":   {"mortality_mult": 1.02, "fertility_mult": 0.93, "morale_baseline": -2.0},
    "last_of_earth_survivors": {"mortality_mult": 1.08, "fertility_mult": 0.85, "morale_baseline": -7.0},
    "utopian_commune":         {"mortality_mult": 0.96, "fertility_mult": 1.08, "morale_baseline": 8.0},
    "military_garrison":       {"mortality_mult": 0.92, "fertility_mult": 0.78, "morale_baseline": -1.0},
    "mixed_voluntary":         {"mortality_mult": 1.00, "fertility_mult": 1.00, "morale_baseline": 0.0},
}


class EthosSystem(BaseSystem):
    name = "ethos"
    dependencies: list[str] = []

    def defaults(self, config: dict) -> dict:
        ethos_id = config.get("population", {}).get("ethos", "mixed_voluntary")
        eff = ETHOS_EFFECTS.get(ethos_id, ETHOS_EFFECTS["mixed_voluntary"])
        return {
            "ethos_id": ethos_id,
            "mortality_mult": eff["mortality_mult"],
            "fertility_mult": eff["fertility_mult"],
            "morale_baseline": eff["morale_baseline"],
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        # Re-emit modifiers each tick so they persist in the lanes
        world.events.emit(Event(
            kind="ethos_active", source=self.name, tick=world.tick,
            effects=(
                Effect(path="mortality.modifiers.ethos", op="set", value=slice_["mortality_mult"]),
                Effect(path="fertility.modifiers.ethos", op="set", value=slice_["fertility_mult"]),
            ),
        ))

    def emit_snapshot(self, world: Any) -> dict:
        return dict(world.state[self.name])
