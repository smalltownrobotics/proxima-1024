"""PathogenSystem — pathogen pressure, outbreaks, microbiome drift in closed-loop ship.

Owns:    world.state['pathogens']
Reads:   population.alive, resources.medicine_adequacy, biology.tier_id,
         population.by_generation
Emits via Effects: mortality.modifiers.pathogens, fertility.modifiers.pathogens,
         morale impact
Emits Events: outbreak (kind: viral_reactivation | bacterial_resistance |
              fungal_environmental | gut_dysbiosis | microbiome_collapse)

A generation ship is a closed loop. The outside-pathogen burden Earth-bound
populations carry (vector-borne, zoonotic, soil-borne, water-borne) is largely
*absent*. But the inside burden compounds: commensal flora evolve, hygiene
infrastructure degrades, the microbiome drifts from Earth baseline as ship-born
generations grow up without natural exposure, latent viruses reactivate under
chronic stress, and antibiotic resistance accumulates in the small confined
gene pool.

Anchored in:
- Mir/ISS biology: Aspergillus on walls, EBV/HSV reactivation in every
  long-duration mission, increased Salmonella virulence under microgravity,
  selective bacterial enrichment.
- Hygiene hypothesis: Strachan 1989, Rook 'old friends' 2003 — reduced microbiome
  diversity drives autoimmune/atopic diseases.
- Antibiotic resistance: WHO global surveillance reports 2014-2024.
- Closed-loop habitats: Biosphere 2 microbial succession (Allen 1991, 1993).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Effect, Event  # noqa: E402


# Pathogen kind catalog. Each kind has expected severity range, duration, and
# what it affects. Severity = fraction of population at risk per outbreak-year.
PATHOGEN_KINDS = {
    "viral_reactivation": {
        "severity_range": (0.04, 0.10),
        "duration_years": (1, 2),
        "weight": 0.30,
        "morale_impact": -8,
        "fertility_impact": 0.92,
        "anchored_in": "Mehta et al 2017 (NASA Twins Study); Crucian et al 2020 'Immune system dysregulation during spaceflight'; ISS EBV/HSV reactivation prevalence (>50% in 6mo+ missions)",
    },
    "bacterial_resistance": {
        "severity_range": (0.08, 0.20),
        "duration_years": (2, 5),
        "weight": 0.25,
        "morale_impact": -15,
        "fertility_impact": 0.95,
        "anchored_in": "WHO Global Antimicrobial Resistance Surveillance 2014-2024; Holmes et al 2016 Lancet 'Understanding the mechanisms and drivers of antimicrobial resistance'",
    },
    "fungal_environmental": {
        "severity_range": (0.04, 0.12),
        "duration_years": (3, 8),
        "weight": 0.20,
        "morale_impact": -10,
        "fertility_impact": 0.97,
        "anchored_in": "Mir Aspergillus colonization 1988-2001; Novikova et al 2006 'Survey of environmental biocontamination on board the International Space Station'; Vesper et al 2008",
    },
    "gut_dysbiosis": {
        "severity_range": (0.06, 0.18),
        "duration_years": (1, 3),
        "weight": 0.15,
        "morale_impact": -12,
        "fertility_impact": 0.88,
        "anchored_in": "Voorhies et al 2019 'Study of the impact of long-duration space missions on the human gut microbiome'; C. difficile outbreak literature in confined populations",
    },
    "microbiome_collapse": {
        "severity_range": (0.15, 0.35),
        "duration_years": (5, 15),
        "weight": 0.10,
        "morale_impact": -25,
        "fertility_impact": 0.80,
        "anchored_in": "Strachan 1989 hygiene hypothesis; Rook 2003 'old friends' theory; Biosphere 2 microbial collapse 1991-1993; Blaser 'Missing Microbes' 2014",
    },
}


# Biology-tier modifiers for pathogen vulnerability (lower = better mitigation)
BIOLOGY_PATHOGEN_MITIGATION = {
    "medical_current": 1.00,
    "medical_regenerative_basic": 0.85,
    "medical_regenerative_advanced": 0.65,
    "nano_medical": 0.40,
    "gene_therapy_radical": 0.25,
}


class PathogenSystem(BaseSystem):
    name = "pathogens"
    dependencies = ["population", "biology", "resources"]
    emits = ["outbreak"]

    def defaults(self, config: dict) -> dict:
        return {
            "pressure": 0.10,
            "hygiene_erosion": 0.0,
            "antibiotic_resistance": 0.05,
            "microbiome_dysbiosis": 0.0,
            "active_outbreak": False,
            "outbreak_kind": None,
            "outbreak_severity": 0.0,
            "outbreak_years_remaining": 0,
            "outbreaks_total": 0,
            "outbreak_history": [],
            "since_last_outbreak": 0,
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        rng = world.rng.fork(self.name)
        alive = max(1, world.read("population.alive") or 1)
        med_ad = world.read("resources.medicine_adequacy") or 1.0
        bio_tier = world.read("biology.tier_id") or "medical_current"
        bio_mit = BIOLOGY_PATHOGEN_MITIGATION.get(bio_tier, 1.00)

        # Generations on board — drives microbiome dysbiosis (ship-born have no
        # natural soil/wild exposure)
        gens = world.read("population.by_generation") or {}
        try:
            max_gen = max((int(k) for k in gens.keys()), default=0)
        except (ValueError, TypeError):
            max_gen = 0

        # Crowding proxy: alive / agricultural_area (as a rough density signal)
        ag_area = world.read("resources.agricultural_area_m2") or max(1, alive * 20)
        density = alive / max(1.0, ag_area / 20.0)  # 1.0 = comfortable, >1 = crowded

        # 1. Hygiene erosion: accumulates in time, accelerated by crowding + low medicine,
        #    decelerated by biology tech. Caps at 1.0.
        erosion_rate = 0.004 * density * (2.0 - med_ad) * bio_mit * dt_years
        slice_["hygiene_erosion"] = max(0.0, min(1.0, slice_["hygiene_erosion"] + erosion_rate))

        # 2. Antibiotic resistance: grows with use (proxied by alive × medicine_use intensity).
        #    Each tick adds a small amount; rare full reset only via gene_therapy_radical tier.
        ar_rate = 0.0035 * (med_ad ** 0.5) * dt_years    # use → selection pressure
        slice_["antibiotic_resistance"] = max(0.0, min(1.0, slice_["antibiotic_resistance"] + ar_rate))

        # 3. Microbiome dysbiosis: drifts with generations; gene-tier biotech can reverse some.
        target_dysbiosis = min(0.85, 0.10 + 0.12 * max_gen)
        if bio_tier in ("nano_medical", "gene_therapy_radical"):
            target_dysbiosis *= 0.5      # active microbiome reconstitution
        slice_["microbiome_dysbiosis"] += (target_dysbiosis - slice_["microbiome_dysbiosis"]) * 0.05 * dt_years
        slice_["microbiome_dysbiosis"] = max(0.0, min(1.0, slice_["microbiome_dysbiosis"]))

        # 4. Pressure composes the three drivers, scaled by biology mitigation.
        pressure = 0.05 + (
            0.40 * slice_["hygiene_erosion"]
            + 0.30 * slice_["antibiotic_resistance"]
            + 0.30 * slice_["microbiome_dysbiosis"]
        )
        pressure *= bio_mit
        slice_["pressure"] = max(0.0, min(1.0, pressure))

        # 5. Outbreak handling
        slice_["since_last_outbreak"] += dt_years
        mort_lane_value = 1.0
        fert_lane_value = 1.0
        if slice_["active_outbreak"]:
            # Active outbreak: apply mortality + fertility hit. Decay severity slightly.
            kind_data = PATHOGEN_KINDS.get(slice_["outbreak_kind"], {})
            sev = slice_["outbreak_severity"]
            mort_lane_value *= 1.0 + sev * 4.0           # ×5 mortality at sev=1.0
            fert_lane_value *= kind_data.get("fertility_impact", 0.95)
            slice_["outbreak_years_remaining"] -= dt_years
            slice_["outbreak_severity"] *= 0.85
            if slice_["outbreak_years_remaining"] <= 0 or slice_["outbreak_severity"] < 0.01:
                slice_["active_outbreak"] = False
                slice_["outbreak_kind"] = None
                slice_["outbreak_severity"] = 0.0
                slice_["outbreak_years_remaining"] = 0
                slice_["since_last_outbreak"] = 0
                # Outbreaks slightly reduce hygiene_erosion (forcing remediation)
                slice_["hygiene_erosion"] = max(0.0, slice_["hygiene_erosion"] - 0.10)
        else:
            # Outbreak roll. Probability scales with pressure, baseline 0.005/yr at pressure=0.1.
            outbreak_chance = 0.05 * slice_["pressure"] * dt_years
            if rng.random() < outbreak_chance:
                kind = self._pick_outbreak_kind(rng, slice_)
                kind_data = PATHOGEN_KINDS[kind]
                lo, hi = kind_data["severity_range"]
                # Severity scaled by hygiene erosion (worse environment → worse outbreaks)
                severity = lo + (hi - lo) * (0.4 + 0.6 * slice_["hygiene_erosion"]) * (rng.random() * 0.5 + 0.5)
                duration_lo, duration_hi = kind_data["duration_years"]
                duration = duration_lo + (duration_hi - duration_lo) * rng.random()
                slice_["active_outbreak"] = True
                slice_["outbreak_kind"] = kind
                slice_["outbreak_severity"] = severity
                slice_["outbreak_years_remaining"] = duration
                slice_["outbreaks_total"] += 1
                slice_["outbreak_history"].append({
                    "tick": world.tick, "kind": kind, "severity": round(severity, 3),
                    "duration": round(duration, 1),
                })
                world.events.emit(Event(
                    kind="outbreak", source=self.name, tick=world.tick,
                    payload={
                        "outbreak_kind": kind,
                        "severity": round(severity, 3),
                        "duration_years": round(duration, 1),
                        "biology_tier": bio_tier,
                    },
                    anchored_in=kind_data.get("anchored_in"),
                ))
                # Apply this tick's first hit immediately
                mort_lane_value *= 1.0 + severity * 4.0
                fert_lane_value *= kind_data.get("fertility_impact", 0.95)

        # Emit modifier-lane updates
        world.events.emit(Event(
            kind="pathogen_pressure_active", source=self.name, tick=world.tick,
            effects=(
                Effect(path="mortality.modifiers.pathogens", op="set", value=round(mort_lane_value, 3)),
                Effect(path="fertility.modifiers.pathogens", op="set", value=round(fert_lane_value, 3)),
            ),
        ))

    def _pick_outbreak_kind(self, rng: Any, slice_: dict) -> str:
        """Pick which kind of outbreak fires, weighted and conditional on current state."""
        weights = {}
        for kind, info in PATHOGEN_KINDS.items():
            w = info["weight"]
            # Boost certain kinds based on current state
            if kind == "bacterial_resistance":
                w *= 1.0 + slice_["antibiotic_resistance"] * 2.0
            elif kind == "fungal_environmental":
                w *= 1.0 + slice_["hygiene_erosion"] * 1.5
            elif kind == "microbiome_collapse":
                # Only fires when dysbiosis is high
                if slice_["microbiome_dysbiosis"] < 0.5:
                    w *= 0.05
                else:
                    w *= 1.0 + (slice_["microbiome_dysbiosis"] - 0.5) * 4.0
            elif kind == "gut_dysbiosis":
                w *= 1.0 + slice_["microbiome_dysbiosis"] * 1.5
            weights[kind] = w
        total = sum(weights.values())
        roll = rng.random() * total
        cum = 0.0
        for kind, w in weights.items():
            cum += w
            if roll < cum:
                return kind
        return list(weights.keys())[0]

    def emit_snapshot(self, world: Any) -> dict:
        s = world.state[self.name]
        return {
            "pressure": round(s["pressure"], 3),
            "hygiene_erosion": round(s["hygiene_erosion"], 3),
            "antibiotic_resistance": round(s["antibiotic_resistance"], 3),
            "microbiome_dysbiosis": round(s["microbiome_dysbiosis"], 3),
            "active_outbreak": s["active_outbreak"],
            "outbreak_kind": s["outbreak_kind"],
            "outbreak_severity": round(s["outbreak_severity"], 3),
            "outbreaks_total": s["outbreaks_total"],
            "since_last_outbreak": round(s["since_last_outbreak"], 1),
        }
