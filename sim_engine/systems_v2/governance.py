"""Dynamic Governance system — stateful regime with legitimacy, pressure, transitions.

Owns:    world.state['governance']
Reads:   world.state['population']['alive'], world.state['morale'] (when present)
Emits:   'governance_transition', 'council_renewal', 'legitimacy_crash',
         'succession_crisis', 'elite_drift'

Per-tick behavior:
  1. Apply baseline legitimacy decay (rate from data/governance.yaml).
  2. Accumulate pressure from morale deficit + crowding + recent deaths.
  3. Refresh legitimacy if conditions are good (high morale, low deaths, food adequate).
  4. Apply governance-type-specific event triggers (council renewal, succession crisis).
  5. Check transition threshold; if breached, sample new regime from transition_to matrix.

Tick order:  population → morale → governance  (declared via dependencies).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GOV_DATA: dict | None = None


def load_governance_data() -> dict:
    global _GOV_DATA
    if _GOV_DATA is None:
        with open(DATA_DIR / "governance.yaml") as f:
            doc = yaml.safe_load(f)
        _GOV_DATA = {entry["id"]: entry for entry in doc["governance"]}
    return _GOV_DATA


class GovernanceSystem(BaseSystem):
    name = "governance"
    dependencies = ["population", "morale"]
    emits = [
        "governance_transition", "council_renewal", "legitimacy_crash",
        "succession_crisis", "elite_drift",
    ]
    subscribes: list[str] = []

    def defaults(self, config: dict) -> dict:
        gov_id = config.get("policy", {}).get("governance", "ai_assisted_council")
        gov_data = load_governance_data().get(gov_id)
        if gov_data is None:
            gov_id = "council"
            gov_data = load_governance_data()["council"]
        crew = int(config.get("population", {}).get("initial", 100))
        special = gov_data.get("special", {}) or {}
        return {
            "type": gov_id,
            "enforcement": gov_data["enforcement"],
            "legitimacy": 0.85,
            "ai_legitimacy": 0.95 if "ai" in gov_data["enforcement"] else None,
            "council_legitimacy": (
                0.85 if gov_data["enforcement"] in ("ai_with_human_oversight", "consensus", "expert_authority") else None
            ),
            "pressure": 0.10,
            "years_in_power": 0,
            "council_size": _council_size_for(crew, special),
            "council_renewal_due_in_years": int(special.get("council_renewal_period_years", 0)),
            "transitions_total": 0,
            "history": [{"type": gov_id, "started_year": 0, "reason": "founding"}],
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        gov_data = load_governance_data()[slice_["type"]]
        special = gov_data.get("special", {}) or {}

        slice_["years_in_power"] += dt_years

        # 1. Legitimacy decay
        decay = float(gov_data.get("legitimacy_decay_per_year", 0.005)) * dt_years
        slice_["legitimacy"] = max(0.0, slice_["legitimacy"] - decay)
        if slice_["ai_legitimacy"] is not None:
            ai_decay = float(special.get("ai_legitimacy_decay", 0.001)) * dt_years
            slice_["ai_legitimacy"] = max(0.0, slice_["ai_legitimacy"] - ai_decay)
        if slice_["council_legitimacy"] is not None:
            council_decay = float(special.get("council_legitimacy_decay", 0.012)) * dt_years
            slice_["council_legitimacy"] = max(0.0, slice_["council_legitimacy"] - council_decay)

        # 2. Pressure accumulation
        morale = world.read("morale.aggregate")
        if morale is None:
            morale = world.read("morale.value") or 70.0
        morale_deficit = max(0.0, 50.0 - morale) / 50.0  # 0 if morale >= 50, ramp to 1 at morale=0
        pressure_rate = float(gov_data.get("pressure_build_rate", 0.012)) * dt_years
        pressure_delta = pressure_rate * (1.0 + 2.0 * morale_deficit)
        # Recent population shock
        deaths = world.read("population.deaths_year") or 0
        alive = max(1, world.read("population.alive") or 1)
        if deaths > alive * 0.05:
            pressure_delta += 0.05      # mass-mortality event spikes pressure
        slice_["pressure"] = min(1.0, slice_["pressure"] + pressure_delta)

        # 3. Legitimacy refresh under good conditions — applies to all legitimacy components.
        # Real institutions get small daily/yearly legitimacy boosts when things are going well
        # (food adequate, low conflict, leaders perceived as competent).
        if morale > 65 and slice_["pressure"] < 0.4:
            refresh = 0.006 * dt_years
            slice_["legitimacy"] = min(1.0, slice_["legitimacy"] + refresh)
            if slice_["council_legitimacy"] is not None:
                slice_["council_legitimacy"] = min(1.0, slice_["council_legitimacy"] + refresh)
            if slice_["ai_legitimacy"] is not None:
                slice_["ai_legitimacy"] = min(1.0, slice_["ai_legitimacy"] + refresh * 0.5)
        # Pressure relaxation under good conditions (not just legitimacy)
        if morale > 60 and deaths < alive * 0.01:
            slice_["pressure"] = max(0.05, slice_["pressure"] - 0.008 * dt_years)

        # 4. Council renewal cycle (for governance types with councils)
        # Renewal is routine institutional refresh — distinct from a transition. It bumps
        # council_legitimacy noticeably and adds only minimal pressure (the institution is
        # working as designed, not failing). Pressure spike is reserved for crisis-driven
        # renewals which we don't model here yet.
        if slice_["council_renewal_due_in_years"] is not None and slice_["council_renewal_due_in_years"] > 0:
            slice_["council_renewal_due_in_years"] = max(0, slice_["council_renewal_due_in_years"] - dt_years)
        if slice_["council_legitimacy"] is not None and slice_["council_renewal_due_in_years"] == 0:
            bump = float(special.get("council_renewal_legitimacy_bump", 0.20))
            volatility_spike = float(special.get("council_renewal_volatility_spike", 0.05))
            slice_["council_legitimacy"] = min(1.0, (slice_.get("council_legitimacy") or 0.5) + bump)
            slice_["pressure"] = min(1.0, slice_["pressure"] + volatility_spike)
            slice_["council_renewal_due_in_years"] = int(special.get("council_renewal_period_years", 25))
            world.events.emit(Event(
                kind="council_renewal", source=self.name, tick=world.tick,
                payload={
                    "council_legitimacy_after": round(slice_["council_legitimacy"], 3),
                    "type": "routine",
                },
            ))

        # 5. Hereditary succession crisis
        if slice_["enforcement"] == "hereditary":
            period = float(special.get("succession_crisis_period_years", 25))
            if slice_["years_in_power"] > 0 and slice_["years_in_power"] % period < dt_years:
                slice_["pressure"] = min(1.0, slice_["pressure"] + float(special.get("succession_crisis_pressure_spike", 0.20)))
                slice_["legitimacy"] = max(0.0, slice_["legitimacy"] - 0.10)
                world.events.emit(Event(
                    kind="succession_crisis", source=self.name, tick=world.tick,
                    payload={"legitimacy_after": slice_["legitimacy"]},
                ))

        # 6. Elite drift trigger (ai_assisted_council specific)
        if slice_["type"] == "ai_assisted_council":
            cur_gen = self._current_generation(world)
            if cur_gen >= 4 and slice_.get("council_legitimacy") is not None and slice_["council_legitimacy"] < 0.40:
                if not slice_.get("_elite_drift_emitted", False):
                    slice_["_elite_drift_emitted"] = True
                    slice_["pressure"] = min(1.0, slice_["pressure"] + 0.15)
                    world.events.emit(Event(
                        kind="elite_drift", source=self.name, tick=world.tick,
                        payload={"generation": cur_gen, "council_legitimacy": slice_["council_legitimacy"]},
                    ))

        # 7. Transition check
        threshold = gov_data.get("transition_threshold", {})
        leg_below = float(threshold.get("legitimacy_below", 0.20))
        press_above = float(threshold.get("pressure_above", 0.65))
        # Effective legitimacy is min(component-legitimacies) — weakest layer determines collapse
        eff_leg = slice_["legitimacy"]
        if slice_["ai_legitimacy"] is not None:
            eff_leg = min(eff_leg, slice_["ai_legitimacy"])
        if slice_["council_legitimacy"] is not None:
            eff_leg = min(eff_leg, slice_["council_legitimacy"])
        if eff_leg < leg_below and slice_["pressure"] > press_above:
            self._transition(world, slice_, gov_data)

    def _transition(self, world: Any, slice_: dict, gov_data: dict) -> None:
        """Pick a new regime from transition_to weights and reset state."""
        rng = world.rng.fork(self.name)
        options = gov_data.get("transition_to", []) or [{"to": "council", "weight": 1.0}]
        # filter out 'schism' pseudo-state for now (TODO: implement schism mechanic)
        options = [o for o in options if o["to"] != "schism"] or [{"to": "council", "weight": 1.0}]
        total_w = sum(o["weight"] for o in options)
        roll = rng.random() * total_w
        cum = 0.0
        chosen = options[-1]["to"]
        for o in options:
            cum += o["weight"]
            if roll < cum:
                chosen = o["to"]
                break
        old_type = slice_["type"]
        old_years = slice_["years_in_power"]
        new_data = load_governance_data().get(chosen, load_governance_data()["council"])
        new_special = new_data.get("special", {}) or {}
        crew = max(1, world.read("population.alive") or 1)
        slice_["type"] = chosen
        slice_["enforcement"] = new_data["enforcement"]
        slice_["legitimacy"] = 0.65   # transitions get a honeymoon
        slice_["ai_legitimacy"] = 0.85 if "ai" in new_data["enforcement"] else None
        slice_["council_legitimacy"] = (
            0.70 if new_data["enforcement"] in ("ai_with_human_oversight", "consensus", "expert_authority") else None
        )
        slice_["pressure"] = 0.20
        slice_["years_in_power"] = 0.0
        slice_["council_size"] = _council_size_for(crew, new_special)
        slice_["council_renewal_due_in_years"] = int(new_special.get("council_renewal_period_years", 0))
        slice_["transitions_total"] += 1
        slice_["_elite_drift_emitted"] = False
        slice_["history"].append({
            "type": chosen,
            "started_year": world.tick,
            "reason": f"transition_from_{old_type}_after_{old_years:.0f}yr",
        })
        world.events.emit(Event(
            kind="governance_transition", source=self.name, tick=world.tick,
            payload={"from": old_type, "to": chosen, "years_in_old_regime": round(old_years, 1)},
        ))

    def _current_generation(self, world: Any) -> int:
        """Pull the highest generation index from the population system."""
        pop = world.state.get("population", {})
        gens = pop.get("by_generation") if isinstance(pop, dict) else None
        if gens:
            return max(int(k) for k in gens.keys()) if gens else 0
        # Fallback: walk people
        people = pop.get("people") if isinstance(pop, dict) else None
        if not people:
            return 0
        return max((p.get("generation", 0) for p in people.values()), default=0)

    def emit_snapshot(self, world: Any) -> dict:
        slice_ = world.state[self.name]
        return {
            "type": slice_["type"],
            "enforcement": slice_["enforcement"],
            "legitimacy": round(slice_["legitimacy"], 3),
            "ai_legitimacy": (None if slice_["ai_legitimacy"] is None else round(slice_["ai_legitimacy"], 3)),
            "council_legitimacy": (None if slice_["council_legitimacy"] is None else round(slice_["council_legitimacy"], 3)),
            "pressure": round(slice_["pressure"], 3),
            "years_in_power": round(slice_["years_in_power"], 1),
            "council_size": slice_["council_size"],
            "transitions_total": slice_["transitions_total"],
            "history": list(slice_["history"]),
        }


def _council_size_for(crew: int, special: dict) -> int | None:
    floor = special.get("council_size_floor")
    ceiling = special.get("council_size_ceiling")
    if floor is None and ceiling is None:
        return None
    floor = floor or 5
    ceiling = ceiling or 13
    # Scale with crew size, clamped
    size = max(floor, min(ceiling, int(round(0.005 * crew))))
    return size
