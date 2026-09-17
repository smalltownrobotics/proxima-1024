"""FactionsSystem — segments population into sub-cohorts that drift independently.

Owns:    world.state['factions']
Reads:   world.state['population'], world.state['morale'], world.state['governance']
Emits:   'faction_drift' (informational)

Initial faction breakdown comes from data/factions_defaults.yaml keyed by ethos.
Per tick: faction shares drift slowly; alienation accumulates from food/morale
deficits weighted by class_tier; intensity persists with mild decay.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Event  # noqa: E402

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FACTIONS_DATA: dict | None = None


def load_factions_defaults() -> dict:
    global _FACTIONS_DATA
    if _FACTIONS_DATA is None:
        with open(DATA_DIR / "factions_defaults.yaml") as f:
            _FACTIONS_DATA = yaml.safe_load(f)["defaults"]
    return _FACTIONS_DATA


class FactionsSystem(BaseSystem):
    name = "factions"
    dependencies = ["population", "morale"]
    emits = ["faction_drift"]

    def defaults(self, config: dict) -> dict:
        ethos = config.get("population", {}).get("ethos", "mixed_voluntary")
        defaults = load_factions_defaults().get(ethos) or load_factions_defaults()["mixed_voluntary"]
        # Build faction dict keyed by id
        factions: dict[str, dict] = {}
        for f in defaults["factions"]:
            factions[f["id"]] = {
                "name": f["name"],
                "share": float(f["share"]),
                "intensity": float(f["intensity"]),
                "political_lean": float(f.get("political_lean", 0.0)),
                "class_tier": float(f.get("class_tier", 0.5)),
                "alienation": float(f.get("alienation_baseline", 0.10)),
                "religious_alignment": f.get("religious_alignment"),
            }
        return {
            "factions": factions,
            "ethos": ethos,
            "drift_events_total": 0,
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        morale = world.read("morale.aggregate") or 70.0
        food_adequacy = world.read("resources.food_adequacy") or 1.0
        gov_legitimacy = world.read("governance.legitimacy") or 0.7
        crowding = world.read("crowding.value") or 1.0

        rng = world.rng.fork(self.name)
        for fid, f in slice_["factions"].items():
            # Alienation drifts up under deprivation, weighted by class_tier (lower-tier
            # factions feel deprivation more). Drifts down under good conditions.
            stress = max(0.0, (50 - morale) / 50.0) * (1.0 + 0.6 * (1.0 - f["class_tier"]))
            stress += max(0.0, (1.0 - food_adequacy)) * 0.5
            stress += max(0.0, (0.5 - gov_legitimacy)) * 0.3
            # Alienation accumulates slowly under sustained stress, decays under good conditions.
            # Rate calibrated so a faction needs 30+yr of sustained 0.5-stress to reach 0.5 alienation.
            f["alienation"] += (stress - 0.10) * 0.025 * dt_years
            f["alienation"] = max(0.0, min(1.0, f["alienation"]))

            # Intensity drifts toward target = 0.4 + 0.5 × alienation (stressed factions intensify)
            target_intensity = 0.4 + 0.5 * f["alienation"]
            f["intensity"] += (target_intensity - f["intensity"]) * 0.02 * dt_years
            f["intensity"] = max(0.1, min(1.0, f["intensity"]))

            # Share drifts very slowly via random walk weighted by intensity
            drift = rng.gauss(0, 0.002) * f["intensity"] * dt_years
            f["share"] = max(0.001, f["share"] + drift)

        # Renormalize shares
        total = sum(f["share"] for f in slice_["factions"].values())
        if total > 0:
            for f in slice_["factions"].values():
                f["share"] /= total

    def emit_snapshot(self, world: Any) -> dict:
        slice_ = world.state[self.name]
        return {
            "factions": {
                fid: {
                    "name": f["name"],
                    "share": round(f["share"], 4),
                    "intensity": round(f["intensity"], 3),
                    "alienation": round(f["alienation"], 3),
                    "class_tier": round(f["class_tier"], 2),
                }
                for fid, f in slice_["factions"].items()
            },
            "max_alienation": round(max((f["alienation"] for f in slice_["factions"].values()), default=0), 3),
            "drift_events_total": slice_["drift_events_total"],
        }
