"""Morale system — owns world.state['morale'].aggregate.

Composes morale from:
- ethos baseline (e.g., religious_refugees +7, military_garrison -1)
- governance baseline (e.g., theocracy +4, military -3)
- death rate penalty (recent shock)
- resource adequacy penalty (food/water/medicine shortage)
- faction alienation penalty (max_alienation across factions)
- governance legitimacy penalty (when legitimacy is collapsing)

Each tick the morale moves 35% toward its computed target (slow tracking).

Naming kept as 'morale_stub' for module path stability; it's no longer a stub.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem  # noqa: E402


class MoraleStub(BaseSystem):
    name = "morale"
    # No 'governance' dep — governance depends on morale, so we read governance from prev tick
    dependencies = ["population", "ethos"]

    def defaults(self, config: dict) -> dict:
        return {"aggregate": 70.0, "last_target": 70.0}

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]

        # 1. Baseline composition: ethos + governance baselines stack
        ethos_baseline = world.read("ethos.morale_baseline") or 0.0
        gov_baseline = self._gov_baseline(world)
        target = 65.0 + ethos_baseline + gov_baseline

        # 2. Death-rate shock
        deaths = world.read("population.deaths_year") or 0
        alive = max(1, world.read("population.alive") or 1)
        death_rate = deaths / alive
        if death_rate > 0.10:
            target -= 50.0
        elif death_rate > 0.05:
            target -= 30.0
        elif death_rate > 0.02:
            target -= 15.0
        elif death_rate > 0.005:
            target -= 5.0

        # 3. Resource adequacy — food first (most affecting), then water, medicine
        food_ad = world.read("resources.food_adequacy")
        water_ad = world.read("resources.water_adequacy")
        med_ad = world.read("resources.medicine_adequacy")
        if food_ad is not None and food_ad < 1.0:
            target -= (1.0 - food_ad) * 30.0
        if water_ad is not None and water_ad < 1.0:
            target -= (1.0 - water_ad) * 15.0
        if med_ad is not None and med_ad < 0.7:
            target -= (0.7 - med_ad) * 12.0

        # 4. Faction alienation — only severe alienation drags morale (was 30, too aggressive)
        max_alien = world.read("factions.max_alienation") or 0.0
        if max_alien > 0.4:
            target -= (max_alien - 0.4) * 25.0

        # 5. Governance legitimacy — only crashes drag morale (not gradual decay)
        gov_leg = world.read("governance.legitimacy")
        if gov_leg is not None and gov_leg < 0.4:
            target -= (0.4 - gov_leg) * 30.0

        # Clamp target into a reasonable range, then ease toward it
        target = max(0.0, min(100.0, target))
        slice_["last_target"] = target
        slice_["aggregate"] = 0.65 * slice_["aggregate"] + 0.35 * target
        slice_["aggregate"] = max(0.0, min(100.0, slice_["aggregate"]))

    def _gov_baseline(self, world: Any) -> float:
        """Read governance morale_baseline from data/governance.yaml at the current type."""
        gov = world.state.get("governance", {})
        gov_type = gov.get("type")
        if not gov_type:
            return 0.0
        # Lazy-load from systems_v2.governance module to avoid duplicating data
        try:
            from systems_v2.governance import load_governance_data
            data = load_governance_data().get(gov_type, {})
            return float(data.get("morale_baseline", 0.0))
        except Exception:
            return 0.0

    def emit_snapshot(self, world: Any) -> dict:
        return {
            "aggregate": round(world.state[self.name]["aggregate"], 1),
            "target": round(world.state[self.name].get("last_target", 70.0), 1),
        }
