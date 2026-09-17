"""GeneticsSystem — founder effect, inbreeding pressure, MHC diversity loss.

Owns:    world.state['genetics']
Reads:   population.alive, population.by_generation, biology.tier_id
Writes (via Effects): mortality.modifiers.genetics, fertility.modifiers.genetics
Emits Events: hereditary_disorder_emergence, mhc_homozygosity_warning

Real generation ships face a founder-effect bottleneck. Below ~150-200 unrelated
founders, accumulated inbreeding raises the frequency of recessive disease
alleles and reduces MHC heterozygosity (immune-system diversity).

Anchored in:
- Polynesian voyaging — successful colonies needed >=50 founders, and many failed
  (Lapita / Easter Island / Pitcairn examples).
- Cheetah (Acinonyx jubatus) genetic bottleneck ~10kya — extreme MHC homozygosity,
  abnormally high disease susceptibility (O'Brien et al 1985).
- Old Order Amish founder effect — Ellis-van Creveld syndrome, Mennonite glutaric
  aciduria, etc. (McKusick et al 1964; Kauffman 2004).
- Tasmanian devil DFTD epidemic 1996-present — small effective population +
  MHC monomorphism = transmissible cancer (Siddle & Kaufman 2013).
- French-Canadian (Quebec) founder effect — clustered hereditary disorders
  (Scriver 2001 review).

Effective population size (Ne) is approximated as alive × N_diversity_factor where
N_diversity_factor < 1.0 reflects family clustering and assortative mating.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402


# Biology tier mitigation — gene-therapy can offset bottleneck effects substantially
BIOLOGY_GENETIC_MITIGATION = {
    "medical_current": 1.00,
    "medical_regenerative_basic": 0.90,
    "medical_regenerative_advanced": 0.70,
    "nano_medical": 0.45,
    "gene_therapy_radical": 0.15,    # CRISPR-tier can selectively eliminate disease alleles
}


class GeneticsSystem(BaseSystem):
    name = "genetics"
    dependencies = ["population", "biology"]
    emits = ["hereditary_disorder_emergence", "mhc_homozygosity_warning"]

    def defaults(self, config: dict) -> dict:
        founders = int(config.get("population", {}).get("initial", 200))
        # Initial inbreeding pressure: small founding pop = higher starting baseline.
        # Below 100 founders we're in cheetah territory; below 50 = Pitcairn.
        if founders >= 200:
            initial_pressure = 0.05
        elif founders >= 100:
            initial_pressure = 0.10 + (200 - founders) / 1000.0
        elif founders >= 50:
            initial_pressure = 0.20 + (100 - founders) / 500.0
        else:
            initial_pressure = 0.35 + (50 - founders) / 200.0
        return {
            "founders": founders,
            "pressure": round(initial_pressure, 3),
            "mhc_diversity": max(0.05, min(1.0, founders / 500.0)),  # 1.0 at 500+; ~0.10 at 50
            "disorders_emerged": 0,
            "warnings_emitted": 0,
            "_warned_thresholds": [],
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        rng = world.rng.fork(self.name)
        alive = max(1, world.read("population.alive") or 1)
        bio_tier = world.read("biology.tier_id") or "medical_current"
        bio_mit = BIOLOGY_GENETIC_MITIGATION.get(bio_tier, 1.00)

        gens = world.read("population.by_generation") or {}
        try:
            max_gen = max((int(k) for k in gens.keys()), default=0)
        except (ValueError, TypeError):
            max_gen = 0

        # Effective population size — assume ~50% of alive are reproducing-age,
        # and ~80% of those find partners (assortative + clustering)
        effective_ne = max(2, int(alive * 0.5 * 0.80))

        # Inbreeding pressure accumulates as 1/(2*Ne) per generation; we use dt_years
        # scaled by typical generation length (25yr). Bio mitigation reduces accumulation.
        gen_fraction = dt_years / 25.0
        pressure_inc = (1.0 / (2.0 * effective_ne)) * gen_fraction * bio_mit
        # Plus a generation-count amplifier — each generation cements the bottleneck
        pressure_inc *= 1.0 + 0.05 * max_gen
        slice_["pressure"] = max(0.0, min(1.0, slice_["pressure"] + pressure_inc))

        # MHC diversity erodes slowly even with mitigation, faster at small Ne
        mhc_decay = 0.001 * gen_fraction * bio_mit * (1.0 + 100.0 / max(20, effective_ne))
        slice_["mhc_diversity"] = max(0.0, slice_["mhc_diversity"] - mhc_decay)

        # Threshold-crossing events
        for thr, label in ((0.30, "moderate"), (0.50, "severe"), (0.70, "extreme")):
            if slice_["pressure"] >= thr and label not in slice_["_warned_thresholds"]:
                slice_["_warned_thresholds"].append(label)
                slice_["warnings_emitted"] += 1
                world.events.emit(Event(
                    kind="mhc_homozygosity_warning", source=self.name, tick=world.tick,
                    payload={
                        "severity": label,
                        "pressure": round(slice_["pressure"], 3),
                        "mhc_diversity": round(slice_["mhc_diversity"], 3),
                        "founders": slice_["founders"],
                    },
                    anchored_in="O'Brien et al 1985 cheetah MHC monomorphism; Siddle & Kaufman 2013 Tasmanian devil DFTD; McKusick 1964 Old Order Amish founder disorders",
                ))

        # Discrete hereditary disorder emergence — probabilistic, rises with pressure
        emergence_chance = 0.002 * (slice_["pressure"] ** 2) * dt_years
        if rng.random() < emergence_chance:
            slice_["disorders_emerged"] += 1
            disorder_severity = 0.04 + 0.06 * slice_["pressure"]
            world.events.emit(Event(
                kind="hereditary_disorder_emergence", source=self.name, tick=world.tick,
                payload={
                    "disorder_n": slice_["disorders_emerged"],
                    "severity": round(disorder_severity, 3),
                    "pressure": round(slice_["pressure"], 3),
                },
                anchored_in="Ellis-van Creveld syndrome (Old Order Amish); glutaric aciduria type I (Pennsylvania Mennonite); Quebec founder disorders (Scriver 2001)",
            ))

        # Apply mortality + fertility lane modifiers via Effects
        # Pressure increases mortality, decreases fertility. Bounded so even max pressure
        # is +50% mortality / -30% fertility — survivable but real.
        mort_mult = 1.0 + slice_["pressure"] * 0.5
        fert_mult = 1.0 - slice_["pressure"] * 0.3
        # MHC homozygosity makes pathogen outbreaks worse — fold into mortality slightly
        mhc_factor = 1.0 + (1.0 - slice_["mhc_diversity"]) * 0.15
        mort_mult *= mhc_factor

        world.events.emit(Event(
            kind="genetics_pressure_active", source=self.name, tick=world.tick,
            effects=(
                Effect(path="mortality.modifiers.genetics", op="set", value=round(mort_mult, 3)),
                Effect(path="fertility.modifiers.genetics", op="set", value=round(fert_mult, 3)),
            ),
        ))

    def emit_snapshot(self, world: Any) -> dict:
        s = world.state[self.name]
        return {
            "founders": s["founders"],
            "pressure": round(s["pressure"], 3),
            "mhc_diversity": round(s["mhc_diversity"], 3),
            "disorders_emerged": s["disorders_emerged"],
            "warnings_emitted": s["warnings_emitted"],
        }
