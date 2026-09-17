"""BiologySystem — applies tech-tier-derived modifiers based on launch year.

Owns:    world.state['biology']
Reads:   world.config['mission']['launch_year'] (immutable)
Writes via Effects: mortality.modifiers.biology_tech, fertility.modifiers.biology_tech

Each launch year unlocks specific biotech tiers from tech_timeline. Once locked at
year 0, the modifier is constant for the run (the ship doesn't gain new tech mid-voyage
unless we add a research subsystem later).

Tiers (from tech_timeline.yaml):
- medical_current (CRISPR-tier): mortality_mult 1.0, fertility_mult 1.0   [baseline]
- medical_regenerative_basic (~2055): mortality 0.92, fertility_window +5
- medical_regenerative_advanced (~2130): mortality 0.78, fertility_window +10, longevity +20
- nano_medical (~2220 if added): mortality 0.65, outbreak severity ×0.6
- gene_therapy_radical (~2280 if added): mortality 0.55, founder genetic-bottleneck immune
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402


# Tiered tech availability — pessimistic year ranges (we use the optimistic edge for "available at maturity")
TIER_THRESHOLDS = [
    {"id": "medical_current",                 "year": 2026, "mortality_mult": 1.00, "fertility_mult": 1.00, "longevity_extension": 0,  "outbreak_severity_mult": 1.0},
    {"id": "medical_regenerative_basic",      "year": 2075, "mortality_mult": 0.92, "fertility_mult": 1.05, "longevity_extension": 5,  "outbreak_severity_mult": 0.95},
    {"id": "medical_regenerative_advanced",   "year": 2180, "mortality_mult": 0.78, "fertility_mult": 1.10, "longevity_extension": 20, "outbreak_severity_mult": 0.85},
    {"id": "nano_medical",                    "year": 2240, "mortality_mult": 0.65, "fertility_mult": 1.12, "longevity_extension": 35, "outbreak_severity_mult": 0.60},
    {"id": "gene_therapy_radical",            "year": 2300, "mortality_mult": 0.55, "fertility_mult": 1.15, "longevity_extension": 50, "outbreak_severity_mult": 0.50},
]


def tier_for_year(launch_year: int) -> dict:
    """Return the highest-applicable tier at the given launch year."""
    chosen = TIER_THRESHOLDS[0]
    for t in TIER_THRESHOLDS:
        if launch_year >= t["year"]:
            chosen = t
    return chosen


class BiologySystem(BaseSystem):
    name = "biology"
    dependencies: list[str] = []

    def defaults(self, config: dict) -> dict:
        launch_year = int(config.get("mission", {}).get("launch_year", 2150))
        tier = tier_for_year(launch_year)
        return {
            "tier_id": tier["id"],
            "mortality_mult": tier["mortality_mult"],
            "fertility_mult": tier["fertility_mult"],
            "longevity_extension": tier["longevity_extension"],
            "outbreak_severity_mult": tier["outbreak_severity_mult"],
            "launch_year": launch_year,
        }

    def tick(self, world: Any, dt_years: float) -> None:
        # Biology tier is locked at game start. Each tick we just re-emit the modifier
        # so it persists in the modifier lanes (in case other systems clear it).
        slice_ = world.state[self.name]
        world.events.emit(Event(
            kind="biology_tier_active", source=self.name, tick=world.tick,
            effects=(
                Effect(path="mortality.modifiers.biology_tech", op="set", value=slice_["mortality_mult"]),
                Effect(path="fertility.modifiers.biology_tech", op="set", value=slice_["fertility_mult"]),
            ),
        ))

    def emit_snapshot(self, world: Any) -> dict:
        return dict(world.state[self.name])
