"""TensionSystem — pairwise tension matrix between factions.

Owns:    world.state['tension']
Reads:   world.state['factions'], world.state['governance']
Emits:   'tension_spike' (informational)

Tension between two factions accumulates from divergence in alienation,
intensity, religious alignment, and class tier. Decays slowly under good
governance + high morale.
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


def _load_initial_tensions() -> dict:
    global _FACTIONS_DATA
    if _FACTIONS_DATA is None:
        with open(DATA_DIR / "factions_defaults.yaml") as f:
            _FACTIONS_DATA = yaml.safe_load(f)["defaults"]
    return _FACTIONS_DATA


class TensionSystem(BaseSystem):
    name = "tension"
    dependencies = ["factions", "morale"]
    emits = ["tension_spike"]

    def defaults(self, config: dict) -> dict:
        ethos = config.get("population", {}).get("ethos", "mixed_voluntary")
        defaults = _load_initial_tensions().get(ethos) or _load_initial_tensions()["mixed_voluntary"]
        # Initial tension matrix as nested dict
        matrix: dict[str, dict[str, float]] = {}
        for fa, row in defaults.get("initial_tension_matrix", {}).items():
            matrix[fa] = {fb: float(t) for fb, t in row.items()}
        return {
            "matrix": matrix,
            "max_pair": 0.0,
            "max_pair_ids": [None, None],
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        factions = world.state.get("factions", {}).get("factions", {})
        if not factions:
            return
        morale = world.read("morale.aggregate") or 70.0
        gov_legitimacy = world.read("governance.legitimacy") or 0.7

        # Tension drift: each pair's tension is pulled toward a target derived from
        # their alienation differential + intensity sum + class_tier gap.
        for fa_id, fa in factions.items():
            slice_["matrix"].setdefault(fa_id, {})
            for fb_id, fb in factions.items():
                if fa_id == fb_id:
                    continue
                aliening_gap = abs(fa["alienation"] - fb["alienation"])
                intensity_pressure = (fa["intensity"] + fb["intensity"]) / 2.0
                class_gap = abs(fa.get("class_tier", 0.5) - fb.get("class_tier", 0.5))
                target = min(0.95, aliening_gap * 0.5 + intensity_pressure * 0.3 + class_gap * 0.4)
                # Good morale + high legitimacy: tension drifts toward 0.5 of target (calmer)
                if morale > 70 and gov_legitimacy > 0.6:
                    target *= 0.7
                cur = slice_["matrix"][fa_id].get(fb_id, target * 0.5)
                # Move toward target slowly
                rate = 0.05 * dt_years
                cur += (target - cur) * rate
                slice_["matrix"][fa_id][fb_id] = max(0.0, min(1.0, cur))

        # Track max pair
        max_t = 0.0
        max_ids = [None, None]
        for fa_id, row in slice_["matrix"].items():
            for fb_id, t in row.items():
                if t > max_t:
                    max_t = t
                    max_ids = [fa_id, fb_id]
        slice_["max_pair"] = max_t
        slice_["max_pair_ids"] = max_ids

    def emit_snapshot(self, world: Any) -> dict:
        slice_ = world.state[self.name]
        return {
            "max_pair": round(slice_["max_pair"], 3),
            "max_pair_ids": list(slice_["max_pair_ids"]),
            "matrix": {
                a: {b: round(t, 3) for b, t in row.items()}
                for a, row in slice_["matrix"].items()
            },
        }
