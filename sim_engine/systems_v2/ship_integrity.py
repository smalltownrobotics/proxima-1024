"""ShipIntegritySystem — system failures: engine, hull, life support, computer, reactor.

Owns:    world.state['ship_integrity']
Reads:   population.alive, biology.tier_id, governance.pressure
Writes (via Effects): mortality.modifiers.system_failure, resources.* via discrete shocks
Emits Events: engine_failure, hull_breach, life_support_failure, computer_corruption,
              reactor_scram, hydroponics_blight, radiation_event, cryogenic_failure

Real spacecraft systems have measurable mean-time-between-failure (MTBF). A 400yr
voyage will stochastically encounter multiple subsystem failures even with good
maintenance. The pessimistic case (low biology_tech, governance crisis-distracting,
sustained underprovisioning) compounds these.

Anchored in:
- Apollo 13 1970 (oxygen tank explosion, near-LOC)
- Skylab 1973 (micrometeoroid shield deployment failure on launch)
- MIR fire 1997 (oxygen candle ignition) + Progress collision 1997
- ISS ECLSS Sabatier reactor offline events (2010-2015 documented)
- Columbia STS-107 2003 (foam strike → thermal failure on re-entry)
- Voyager spacecraft (1977-present) thruster degradation, gyroscope wear
- Soyuz MS-09 hull leak 2018 (intentional but illustrative)
- Apollo 1 fire 1967 (atmosphere management)
- Real reactor SCRAM rates: ~0.5/yr/reactor in commercial fleet (NRC data)

The system models a single aggregate `integrity` for the ship, with discrete
failure events drawn from a weighted catalog when probability rolls succeed.
Repair costs are modeled as resource hits + small mortality, not full infrastructure.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402


# Failure kinds with severity + impact ranges. Each kind has:
#   weight: relative probability when a failure roll fires
#   mortality_severity: fraction-of-pop killed range
#   integrity_hit: how much aggregate integrity drops
#   recovery_years: how long the ship operates degraded before back to normal
#   condition: optional predicate; e.g. computer_corruption only on AI regimes
FAILURE_KINDS = {
    "engine_failure": {
        "weight": 0.18,
        "mortality_range": (0.000, 0.005),
        "integrity_hit": 0.10,
        "recovery_years": (5, 15),
        "voyage_extension_years": (5, 30),
        "morale_impact": -10,
        "anchored_in": "Voyager 1/2 thruster degradation (2014-2024); Apollo 13 SPS bypass 1970; Soyuz failures 1971-2024 (n=12 major)",
    },
    "hull_breach": {
        "weight": 0.20,
        "mortality_range": (0.005, 0.04),     # localized but dangerous
        "integrity_hit": 0.08,
        "recovery_years": (1, 4),
        "morale_impact": -15,
        "anchored_in": "Skylab micrometeoroid shield 1973; ISS Cygnus depress 2020; Soyuz MS-09 leak 2018; Columbia STS-107 2003",
    },
    "life_support_failure": {
        "weight": 0.18,
        "mortality_range": (0.005, 0.06),
        "integrity_hit": 0.12,
        "recovery_years": (2, 8),
        "morale_impact": -18,
        "anchored_in": "Apollo 13 1970 (CO2 scrubber); ISS Sabatier reactor offline events 2010-2015; ISS Elektron O2 generator failures 2003-2010",
    },
    "computer_corruption": {
        "weight": 0.10,
        "mortality_range": (0.0, 0.005),
        "integrity_hit": 0.06,
        "recovery_years": (3, 10),
        "morale_impact": -8,
        "condition": "ai_or_algorithmic",
        "ai_legitimacy_hit": 0.30,
        "anchored_in": "Mariner 1 1962 (software bug); Ariane 5 flight 501 1996; F-22 IDL crossing 2007 — software/architecture failures with mission consequences",
    },
    "reactor_scram": {
        "weight": 0.12,
        "mortality_range": (0.001, 0.012),
        "integrity_hit": 0.08,
        "recovery_years": (1, 5),
        "morale_impact": -10,
        "anchored_in": "NRC commercial fleet SCRAM rates ~0.5/yr/reactor; Three Mile Island 1979; Fukushima 2011; SL-1 1961",
    },
    "hydroponics_blight": {
        "weight": 0.10,
        "mortality_range": (0.000, 0.008),
        "integrity_hit": 0.04,
        "recovery_years": (2, 6),
        "food_adequacy_hit": 0.30,
        "morale_impact": -8,
        "anchored_in": "Irish Potato Famine 1845-1849 (Phytophthora infestans); Biosphere 2 crop failures 1991-1993; Norfolk 4-course system failures 19c",
    },
    "radiation_event": {
        "weight": 0.08,
        "mortality_range": (0.001, 0.025),
        "integrity_hit": 0.05,
        "recovery_years": (1, 3),
        "morale_impact": -12,
        "anchored_in": "Carrington Event 1859; AD 774-775 cosmic ray spike; Apollo astronaut chronic-low-dose data; Mars Curiosity RAD instrument 2012",
    },
    "cryogenic_failure": {
        "weight": 0.04,
        "mortality_range": (0.005, 0.02),
        "integrity_hit": 0.05,
        "recovery_years": (1, 2),
        "morale_impact": -10,
        "condition": "cryo_active",
        "anchored_in": "Speculative — based on liquid helium freezer reliability data and IVF cryostorage incident reports (Pacific Fertility 2018)",
    },
}


class ShipIntegritySystem(BaseSystem):
    name = "ship_integrity"
    dependencies = ["population", "biology"]
    emits = list(FAILURE_KINDS.keys())

    def defaults(self, config: dict) -> dict:
        ship_class = config.get("ship", {}).get("class", "ship_modular_cluster")
        # Different ship classes start at different baseline integrity decay rates
        decay_per_year = {
            "ship_oneill_cylinder": 0.0006,        # heavy-duty industrial, slow decay
            "ship_aurora_ark": 0.0008,
            "ship_modular_cluster": 0.0012,        # smaller, more failure-prone
            "ship_generation_ark": 0.0007,
        }.get(ship_class, 0.0010)
        cryo_policy = config.get("policy", {}).get("cryo", "rotation")
        return {
            "integrity": 1.0,
            "base_decay_per_year": decay_per_year,
            "active_failure": None,
            "active_severity": 0.0,
            "active_years_remaining": 0.0,
            "failures_total": 0,
            "failure_history": [],
            "cryo_policy": cryo_policy,
            "voyage_extension_years": 0.0,
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        rng = world.rng.fork(self.name)
        bio_tier = world.read("biology.tier_id") or "medical_current"
        gov_pressure = world.read("governance.pressure") or 0.1
        alive = max(1, world.read("population.alive") or 1)

        # Better biotech reduces failures (better maintenance autonomy / repair)
        bio_factor = {
            "medical_current": 1.00,
            "medical_regenerative_basic": 0.95,
            "medical_regenerative_advanced": 0.85,
            "nano_medical": 0.70,
            "gene_therapy_radical": 0.65,
        }.get(bio_tier, 1.00)

        # Governance crisis distracts maintenance — pressure>0.6 raises failure rate
        crisis_factor = 1.0 + max(0.0, gov_pressure - 0.5) * 1.5

        # Integrity slowly recovers when there's no active failure (routine maintenance);
        # decays at base rate when nothing is happening, faster during failures.
        if not slice_["active_failure"]:
            recovery_rate = 0.001 * dt_years
            slice_["integrity"] = min(1.0, slice_["integrity"] + recovery_rate)
            # Ambient decay
            decay = slice_["base_decay_per_year"] * crisis_factor * dt_years
            slice_["integrity"] = max(0.0, slice_["integrity"] - decay)

        # Failure roll
        mort_lane_value = 1.0
        if slice_["active_failure"]:
            slice_["active_years_remaining"] -= dt_years
            sev = slice_["active_severity"]
            mort_lane_value *= 1.0 + sev * 6.0     # active failure = elevated mortality
            if slice_["active_years_remaining"] <= 0:
                slice_["active_failure"] = None
                slice_["active_severity"] = 0.0
                slice_["active_years_remaining"] = 0.0
                # Repaired — modest integrity recovery
                slice_["integrity"] = min(1.0, slice_["integrity"] + 0.04)
        else:
            # Probability of any failure scales inversely with integrity.
            base_chance = (1.0 - slice_["integrity"]) * 0.05 * bio_factor * crisis_factor * dt_years
            base_chance += 0.005 * dt_years     # background rate even at perfect integrity
            if rng.random() < base_chance:
                kind = self._pick_failure_kind(rng, world, slice_)
                if kind:
                    info = FAILURE_KINDS[kind]
                    sev_lo, sev_hi = info["mortality_range"]
                    severity = sev_lo + (sev_hi - sev_lo) * rng.random()
                    rec_lo, rec_hi = info["recovery_years"]
                    duration = rec_lo + (rec_hi - rec_lo) * rng.random()
                    slice_["active_failure"] = kind
                    slice_["active_severity"] = severity
                    slice_["active_years_remaining"] = duration
                    slice_["integrity"] = max(0.0, slice_["integrity"] - info["integrity_hit"])
                    slice_["failures_total"] += 1
                    slice_["failure_history"].append({
                        "tick": world.tick, "kind": kind,
                        "severity": round(severity, 4), "duration": round(duration, 1),
                    })
                    # Side-effects: voyage extension, food hit, AI legitimacy hit
                    if "voyage_extension_years" in info:
                        ve_lo, ve_hi = info["voyage_extension_years"]
                        slice_["voyage_extension_years"] += ve_lo + (ve_hi - ve_lo) * rng.random()
                    payload = {
                        "kind": kind,
                        "severity": round(severity, 4),
                        "duration_years": round(duration, 1),
                        "integrity_after": round(slice_["integrity"], 3),
                    }
                    effects = []
                    if "food_adequacy_hit" in info:
                        # Translate into a transient pathogen-style mortality bump on resources lane
                        # (simplified — directly bumps food_adequacy via effect)
                        effects.append(Effect(path="resources.food_adequacy",
                                              op="mul", value=1.0 - info["food_adequacy_hit"]))
                    if "ai_legitimacy_hit" in info:
                        effects.append(Effect(path="governance.ai_legitimacy",
                                              op="mul", value=1.0 - info["ai_legitimacy_hit"]))
                    world.events.emit(Event(
                        kind=kind, source=self.name, tick=world.tick,
                        payload=payload, effects=tuple(effects),
                        anchored_in=info.get("anchored_in"),
                    ))
                    # Apply this tick's first hit immediately
                    mort_lane_value *= 1.0 + severity * 6.0

        # Emit modifier-lane update each tick
        world.events.emit(Event(
            kind="ship_integrity_active", source=self.name, tick=world.tick,
            effects=(Effect(path="mortality.modifiers.system_failure",
                            op="set", value=round(mort_lane_value, 3)),),
        ))

    def _pick_failure_kind(self, rng: Any, world: Any, slice_: dict) -> str | None:
        """Choose which failure kind. Filters by condition, weights by integrity-relevant factors."""
        gov_type = (world.state.get("governance", {}) or {}).get("type", "")
        is_ai_regime = gov_type in ("ai_assisted_council", "algorithmic_democracy")
        is_cryo_active = slice_["cryo_policy"] in ("rotation", "primary")

        candidates: dict[str, float] = {}
        for kind, info in FAILURE_KINDS.items():
            cond = info.get("condition")
            if cond == "ai_or_algorithmic" and not is_ai_regime:
                continue
            if cond == "cryo_active" and not is_cryo_active:
                continue
            candidates[kind] = info["weight"]
        if not candidates:
            return None
        total = sum(candidates.values())
        roll = rng.random() * total
        cum = 0.0
        for k, w in candidates.items():
            cum += w
            if roll < cum:
                return k
        return list(candidates.keys())[-1]

    def emit_snapshot(self, world: Any) -> dict:
        s = world.state[self.name]
        return {
            "integrity": round(s["integrity"], 3),
            "active_failure": s["active_failure"],
            "active_severity": round(s["active_severity"], 4),
            "active_years_remaining": round(s["active_years_remaining"], 1),
            "failures_total": s["failures_total"],
            "voyage_extension_years": round(s["voyage_extension_years"], 1),
        }
