#!/usr/bin/env python3
"""Run a config through the v2 framework and print a diagnostic summary.

Usage:
    python3 run_v2.py [config.yaml] [--ticks N] [--seed N] [--diagnostic]
    python3 run_v2.py --compare              # canonical AI 10k vs scrappy religious 300

Without args, runs the comparative pair.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from framework import Orchestrator, Registry, build_world  # noqa: E402
from systems_v2 import (  # noqa: E402
    BiologySystem, EmergentEventsSystem, EthosSystem, FactionsSystem,
    FertilityModifierSystem, GeneticsSystem, GovernanceSystem, MoraleStub,
    MortalityModifierSystem, PathogenSystem, PopulationSystem,
    ResourcesSystem, ShipIntegritySystem, TensionSystem,
)


def build_registry() -> Registry:
    reg = Registry()
    # Modifier carriers first (own their slices, but no-op tick)
    reg.register(MortalityModifierSystem())
    reg.register(FertilityModifierSystem())
    # Sources — write to modifier lanes via Effects
    reg.register(BiologySystem())
    reg.register(EthosSystem())
    reg.register(GeneticsSystem())
    # Population (depends on modifier slices having been allocated)
    reg.register(PopulationSystem())
    # Morale needs population
    reg.register(MoraleStub())
    # Resources (writes to modifier lanes; needs population + morale)
    reg.register(ResourcesSystem())
    # Pathogens (writes to mortality/fertility lanes; needs population, biology, resources)
    reg.register(PathogenSystem())
    # Ship integrity (failures: engine, hull, life support, computer, reactor, etc.)
    reg.register(ShipIntegritySystem())
    # Social
    reg.register(FactionsSystem())
    reg.register(TensionSystem())
    # Institutional
    reg.register(GovernanceSystem())
    # Emergent events on top
    reg.register(EmergentEventsSystem())
    return reg


def run_one(config: dict, ticks: int | None = None, seed: int | None = None,
            diagnostic: bool = False, return_trajectory: bool = False) -> dict:
    if seed is not None:
        config.setdefault("tunables", {})["rng_seed"] = seed
    if ticks is None:
        ticks = int(config.get("mission", {}).get("voyage_years", 200))
    reg = build_registry()
    world = build_world(config)
    orch = Orchestrator(reg, world, dt_years=1.0)
    orch.initialize()
    # Voyage extension: if the ship suffers engine failures, the journey takes longer.
    # We tick year-by-year. Engine_failure events accumulate voyage_extension_years on
    # the ship_integrity slice; we extend the planned tick count up to 2x the original
    # voyage as a hard ceiling (prevents runaway on pathological seeds).
    original_ticks = ticks
    max_ticks = ticks * 2                       # absolute ceiling
    snap0 = {"tick": 0, "sim_year": 0.0, "earth_year": float(world.earth_year),
             "systems": {}, "events": []}
    for sys_ in reg.systems():
        snap0["systems"][sys_.name] = sys_.emit_snapshot(world)
    traj = [snap0]
    while world.tick < ticks:
        traj.append(orch.step())
        if world.tick >= ticks and ticks < max_ticks:
            # Crossed the (current) finish line — check whether engine failures pushed
            # the destination further out.
            ext = (world.state.get("ship_integrity", {}) or {}).get("voyage_extension_years", 0.0)
            target = min(max_ticks, original_ticks + int(ext))
            if target > ticks:
                ticks = target

    # Aggregate diagnostics
    final = traj[-1]
    pop = final["systems"].get("population", {})
    mort = final["systems"].get("mortality", {})
    fert = final["systems"].get("fertility", {})
    gov = final["systems"].get("governance", {})
    factions = final["systems"].get("factions", {})
    tension = final["systems"].get("tension", {})
    morale = final["systems"].get("morale", {})

    # Count events across run
    event_counts: dict[str, int] = {}
    for snap in traj:
        for ev in snap.get("events", []):
            event_counts[ev["kind"]] = event_counts.get(ev["kind"], 0) + 1

    # Mid-run sample: alive and morale at year 50, 100, 200
    samples = {}
    for milestone in (25, 50, 100, 150, 200, 300, 400):
        if milestone < len(traj):
            samples[f"yr{milestone}"] = {
                "alive": traj[milestone]["systems"].get("population", {}).get("alive"),
                "morale": traj[milestone]["systems"].get("morale", {}).get("aggregate"),
                "gov": traj[milestone]["systems"].get("governance", {}).get("type"),
                "leg": traj[milestone]["systems"].get("governance", {}).get("legitimacy"),
                "council_leg": traj[milestone]["systems"].get("governance", {}).get("council_legitimacy"),
                "max_tension": traj[milestone]["systems"].get("tension", {}).get("max_pair"),
            }

    summary = {
        "config_name": config.get("name", "unnamed"),
        "voyage_years": ticks,                           # actual ticks run (may be extended)
        "voyage_years_planned": original_ticks,          # what the config asked for
        "voyage_extension_years": ticks - original_ticks,
        "founding": int(config.get("population", {}).get("initial", 0)),
        "ethos": config.get("population", {}).get("ethos"),
        "governance_initial": config.get("policy", {}).get("governance"),
        "alive_final": pop.get("alive"),
        "births_total": pop.get("births_total"),
        "deaths_total": pop.get("deaths_total"),
        "median_age_final": pop.get("median_age"),
        "morale_final": morale.get("aggregate"),
        "governance_final": gov.get("type"),
        "transitions_total": gov.get("transitions_total"),
        "governance_history": gov.get("history"),
        "max_tension_final": tension.get("max_pair"),
        "max_alienation_final": factions.get("max_alienation"),
        "mortality_modifier_final": mort.get("effective"),
        "fertility_modifier_final": fert.get("effective"),
        "mortality_lanes_final": mort.get("modifiers"),
        "fertility_lanes_final": fert.get("modifiers"),
        "event_counts": event_counts,
        "samples": samples,
    }
    if return_trajectory:
        # Per-year compact timeline for the UI viewer.
        timeline = []
        cum_births = 0
        cum_deaths = 0
        for snap in traj:
            sys = snap.get("systems", {})
            pop = sys.get("population", {}) or {}
            mor = sys.get("morale", {}) or {}
            res = sys.get("resources", {}) or {}
            gov = sys.get("governance", {}) or {}
            fac = sys.get("factions", {}) or {}
            ten = sys.get("tension", {}) or {}
            mort = sys.get("mortality", {}) or {}
            fert = sys.get("fertility", {}) or {}
            cum_births += pop.get("births_year") or 0
            cum_deaths += pop.get("deaths_year") or 0
            pat = sys.get("pathogens", {}) or {}
            gen = sys.get("genetics", {}) or {}
            ship = sys.get("ship_integrity", {}) or {}
            timeline.append({
                "year": snap.get("tick", 0),
                "earth_year": snap.get("earth_year"),
                "alive": pop.get("alive"),
                "deaths_year": pop.get("deaths_year"),
                "births_year": pop.get("births_year"),
                "births_total": cum_births,
                "deaths_total": cum_deaths,
                "median_age": pop.get("median_age"),
                # v1-compat fields kept; v2-specific below
                "crowding": None,
                "active_outbreak": pat.get("active_outbreak", False),
                "outbreak_severity": pat.get("outbreak_severity", 0),
                # v2 state for richer voyage-stat panel
                "pathogen_pressure": pat.get("pressure"),
                "outbreak_kind": pat.get("outbreak_kind"),
                "genetic_pressure": gen.get("pressure"),
                "mhc_diversity": gen.get("mhc_diversity"),
                "ship_integrity": ship.get("integrity"),
                "ship_failure_active": ship.get("active_failure"),
                "voyage_extension_years": ship.get("voyage_extension_years"),
                "by_sex": pop.get("by_sex"),
                "by_generation": pop.get("by_generation"),
                "age_pyramid": pop.get("age_pyramid"),
                "morale": mor.get("aggregate"),
                "food_adequacy": res.get("food_adequacy"),
                "water_adequacy": res.get("water_adequacy"),
                "medicine_adequacy": res.get("medicine_adequacy"),
                "gov_type": gov.get("type"),
                "gov_legitimacy": gov.get("legitimacy"),
                "gov_pressure": gov.get("pressure"),
                "council_legitimacy": gov.get("council_legitimacy"),
                "max_alienation": fac.get("max_alienation"),
                "max_tension": ten.get("max_pair"),
                "mort_eff": mort.get("effective"),
                "fert_eff": fert.get("effective"),
                # Discrete events fired this tick (filtered to interesting kinds — the
                # periodic per-tick modifier-emission events are framework plumbing and
                # never user-facing).
                "events": [
                    {
                        "kind": e.get("kind"),
                        "anchored_in": e.get("anchored_in"),
                        "payload": e.get("payload", {}),
                    }
                    for e in snap.get("events", [])
                    if e.get("kind") not in (
                        "biology_tier_active", "ethos_active", "resource_event",
                        "birth", "death",
                        "ship_integrity_active", "pathogen_pressure_active",
                        "genetics_pressure_active",
                    )
                ],
            })
        summary["trajectory"] = timeline

    if diagnostic:
        # Trace mortality each decade
        decade_trace = []
        for i in range(0, len(traj), 25):
            snap = traj[i]
            decade_trace.append({
                "year": i,
                "alive": snap["systems"].get("population", {}).get("alive"),
                "deaths_year": snap["systems"].get("population", {}).get("deaths_year"),
                "births_year": snap["systems"].get("population", {}).get("births_year"),
                "median_age": snap["systems"].get("population", {}).get("median_age"),
                "morale": snap["systems"].get("morale", {}).get("aggregate"),
                "morale_target": snap["systems"].get("morale", {}).get("target"),
                "max_alien": snap["systems"].get("factions", {}).get("max_alienation"),
                "food_ad": snap["systems"].get("resources", {}).get("food_adequacy"),
                "water_ad": snap["systems"].get("resources", {}).get("water_adequacy"),
                "med_ad": snap["systems"].get("resources", {}).get("medicine_adequacy"),
                "mort_eff": snap["systems"].get("mortality", {}).get("effective"),
                "fert_eff": snap["systems"].get("fertility", {}).get("effective"),
                "gov": snap["systems"].get("governance", {}).get("type"),
                "leg": snap["systems"].get("governance", {}).get("legitimacy"),
                "council_leg": snap["systems"].get("governance", {}).get("council_legitimacy"),
                "pressure": snap["systems"].get("governance", {}).get("pressure"),
            })
        summary["decade_trace"] = decade_trace
    return summary


def print_summary(s: dict) -> None:
    print(f"\n=== {s['config_name']} ===")
    print(f"  ethos={s.get('ethos')} gov_init={s.get('governance_initial')}  "
          f"founders={s['founding']} voyage={s['voyage_years']}yr")
    print(f"  alive_final={s['alive_final']}  births={s['births_total']}  deaths={s['deaths_total']}")
    print(f"  median_age={s['median_age_final']}  morale={s['morale_final']}")
    print(f"  governance: {s['governance_final']} (transitions={s['transitions_total']})")
    print(f"  mort_mod={s['mortality_modifier_final']} fert_mod={s['fertility_modifier_final']}")
    print(f"  mort_lanes={s['mortality_lanes_final']}")
    print(f"  fert_lanes={s['fertility_lanes_final']}")
    print(f"  events: {s['event_counts']}")
    if s.get("samples"):
        print(f"  trajectory:")
        for k, v in s["samples"].items():
            print(f"    {k}: alive={v['alive']} morale={v['morale']} gov={v['gov']} leg={v['leg']} "
                  f"council_leg={v['council_leg']} max_tension={v['max_tension']}")
    if s.get("decade_trace"):
        print(f"  diagnostic trace:")
        for d in s["decade_trace"]:
            print(f"    yr{d['year']:>3}: alive={d['alive']} d={d['deaths_year']} b={d['births_year']} "
                  f"med_age={d['median_age']} morale={d['morale']}(t={d['morale_target']}) "
                  f"alien={d['max_alien']} food={d['food_ad']} mort_eff={d['mort_eff']} "
                  f"fert_eff={d['fert_eff']} gov={d['gov']} leg={d['leg']} c_leg={d['council_leg']} "
                  f"press={d['pressure']}")


def stress_test() -> int:
    """Under-provisioned ship designed to trigger crisis events."""
    cfg_secular_crisis = {
        "name": "secular_under_provisioned_500_200yr",
        "mission": {"voyage_years": 200, "launch_year": 2150},
        "ship": {"agricultural_area_m2": 4000, "habitable_volume_m3": 12000,
                 "water_recovery_efficiency": 0.85, "oxygen_recovery_efficiency": 0.85},
        "population": {
            "initial": 500, "female_fraction": 0.5,
            "age_pyramid": {
                "0-4": 0.06, "5-9": 0.06, "10-14": 0.07, "15-19": 0.09,
                "20-24": 0.13, "25-29": 0.13, "30-34": 0.12, "35-39": 0.11,
                "40-44": 0.09, "45-49": 0.06, "50-54": 0.04, "55-59": 0.02,
                "60-64": 0.02,
            },
            "ethos": "commercial_expedition",
        },
        "policy": {"governance": "council"},
        "tunables": {"rng_seed": 42, "population_ceiling": 1000},
    }
    s = run_one(cfg_secular_crisis, diagnostic=True)
    print_summary(s)
    return 0


def comparative() -> int:
    """Run AI 10k Aurora vs religious 300 first_contact."""
    cfg_ai = {
        "name": "ai_canonical_10k_407yr",
        "mission": {"destination": "trappist_1_e", "voyage_years": 407, "launch_year": 2300},
        "ship": {"agricultural_area_m2": 200000, "habitable_volume_m3": 800000},
        "population": {
            "initial": 10000,
            "female_fraction": 0.5,
            "age_pyramid": {
                "0-4": 0.05, "5-9": 0.05, "10-14": 0.06, "15-19": 0.08,
                "20-24": 0.12, "25-29": 0.13, "30-34": 0.13, "35-39": 0.12,
                "40-44": 0.10, "45-49": 0.07, "50-54": 0.05, "55-59": 0.03,
                "60-64": 0.01,
            },
            "ethos": "mission_scientists",
        },
        "policy": {"governance": "ai_assisted_council"},
        "tunables": {"rng_seed": 42, "population_ceiling": 20000},
    }
    cfg_rel = {
        "name": "scrappy_religious_300_85yr",
        "mission": {"destination": "proxima_centauri_b", "voyage_years": 85, "launch_year": 2150},
        "ship": {"agricultural_area_m2": 6000, "habitable_volume_m3": 24000},
        "population": {
            "initial": 300,
            "female_fraction": 0.5,
            "age_pyramid": {
                "0-4": 0.06, "5-9": 0.06, "10-14": 0.07, "15-19": 0.09,
                "20-24": 0.13, "25-29": 0.13, "30-34": 0.12, "35-39": 0.11,
                "40-44": 0.09, "45-49": 0.06, "50-54": 0.04, "55-59": 0.02,
                "60-64": 0.02,
            },
            "ethos": "religious_refugees",
        },
        "policy": {"governance": "theocracy"},
        "tunables": {"rng_seed": 42, "population_ceiling": 800},
    }
    s1 = run_one(cfg_ai, diagnostic=True)
    s2 = run_one(cfg_rel, diagnostic=True)
    print_summary(s1)
    print_summary(s2)
    out = ROOT / "runs" / "v2_comparative.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps([s1, s2], indent=2, default=str))
    print(f"\nWrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default=None)
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--diagnostic", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--stress", action="store_true")
    args = ap.parse_args()
    if args.stress:
        return stress_test()
    if args.compare or args.config is None:
        return comparative()
    cfg = yaml.safe_load(Path(args.config).read_text())
    s = run_one(cfg, ticks=args.ticks, seed=args.seed, diagnostic=args.diagnostic)
    print_summary(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
