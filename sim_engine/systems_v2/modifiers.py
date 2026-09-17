"""Modifier carrier systems — owners of the multiplicative modifier dicts.

These exist so other systems can write a named lane (e.g.,
`world.state['mortality']['modifiers']['food'] = 1.4`) without breaking
slice ownership: each modifier-owning system is responsible for HOSTING the
slice; other systems write to it via emitted Effects, not direct writes.

For the bootstrap I keep these lightweight — they own a `modifiers` dict and
nothing else. Composition (the multiplicative product) happens on read in the
consuming system (population.mortality, etc.).
"""
from __future__ import annotations

from typing import Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem  # noqa: E402


class MortalityModifierSystem(BaseSystem):
    name = "mortality"
    dependencies: list[str] = []

    def defaults(self, config: dict) -> dict:
        return {"modifiers": {"baseline": 1.0}}

    def tick(self, world: Any, dt_years: float) -> None:
        # No autonomous behavior; other systems write into modifiers via effects.
        pass

    def emit_snapshot(self, world: Any) -> dict:
        mods = world.state[self.name]["modifiers"]
        product = 1.0
        for v in mods.values():
            try:
                product *= float(v)
            except (TypeError, ValueError):
                continue
        return {"modifiers": dict(mods), "effective": round(product, 4)}


class FertilityModifierSystem(BaseSystem):
    name = "fertility"
    dependencies: list[str] = []

    def defaults(self, config: dict) -> dict:
        return {"modifiers": {"baseline": 1.0}}

    def tick(self, world: Any, dt_years: float) -> None:
        pass

    def emit_snapshot(self, world: Any) -> dict:
        mods = world.state[self.name]["modifiers"]
        product = 1.0
        for v in mods.values():
            try:
                product *= float(v)
            except (TypeError, ValueError):
                continue
        return {"modifiers": dict(mods), "effective": round(product, 4)}
