"""Offline engine tests — determinism at scale, conservation, engine seams,
life-history reproducibility and heritability sanity.

Pure engine: no model calls, no network, no bridge imports. Run with
    python3 -m unittest discover -s sim_engine/tests -p 'test_*.py'
from the repo root (or any cwd — paths are absolute below).
"""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
import unittest
from pathlib import Path

SIM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIM))

from framework import build_world, Orchestrator, Registry  # noqa: E402
from run_v2 import build_registry  # noqa: E402
from systems_v2.population import apply_death, recount, trim_founders  # noqa: E402
from life_history import (  # noqa: E402
    BIG_FIVE, GCA_MEAN, GCA_SD, H2_GCA, H2_PERSONALITY, LifeHistoryBook,
    TRAIT_MEAN, TRAIT_SD,
)

PYRAMID = {"0-4": .05, "5-9": .05, "10-14": .06, "15-19": .08, "20-24": .12,
           "25-29": .13, "30-34": .13, "35-39": .12, "40-44": .10, "45-49": .07,
           "50-54": .05, "55-59": .03, "60-64": .01}


def make_config(n: int, years: int, seed: int = 42) -> dict:
    """Bench-style config with manifest scaled to crew (mirrors the bridge)."""
    return {
        "name": f"test_{n}",
        "mission": {"voyage_years": years, "launch_year": 2200},
        "ship": {"habitable_volume_m3": n * 60, "agricultural_area_m2": n * 21,
                 "food_storage_kg": n * 1.2 * 240, "medicine_storage_kg": n * 4,
                 "water_recovery_efficiency": .975, "oxygen_recovery_efficiency": .98},
        "population": {"initial": n, "female_fraction": 0.5, "ethos": "mission_scientists",
                       "age_pyramid": dict(PYRAMID)},
        "policy": {"governance": "technocracy", "cryo": "none"},
        "tunables": {"rng_seed": seed, "population_ceiling": n * 2},
    }


def run_world(n: int, years: int, seed: int = 42, keep_snaps: bool = False):
    world = build_world(make_config(n, years, seed))
    orch = Orchestrator(build_registry(), world, dt_years=1.0)
    orch.initialize()
    snaps = []
    for _ in range(years):
        snap = orch.step()
        if keep_snaps:
            snaps.append(snap)
    return world, snaps


def state_hash(world) -> str:
    return hashlib.sha256(json.dumps(world.state, sort_keys=True, default=str).encode()).hexdigest()


# === Determinism ===
class TestDeterminismAtScale(unittest.TestCase):
    def test_same_seed_same_world(self):
        # Two crew scales, full registry: byte-identical state after 30 years.
        for n in (1024, 5000):
            w1, _ = run_world(n, 30, seed=7)
            w2, _ = run_world(n, 30, seed=7)
            self.assertEqual(state_hash(w1), state_hash(w2), f"divergence at crew={n}")

    def test_different_seed_differs(self):
        w1, _ = run_world(1024, 20, seed=7)
        w2, _ = run_world(1024, 20, seed=8)
        self.assertNotEqual(state_hash(w1), state_hash(w2))


# === Conservation ===
class TestConservation(unittest.TestCase):
    def test_births_deaths_reconcile_every_tick(self):
        world, snaps = run_world(1024, 60, keep_snaps=True)
        prev_alive = None
        for snap in snaps:
            pop = snap["systems"]["population"]
            if prev_alive is not None:
                self.assertEqual(pop["alive"] - prev_alive,
                                 pop["births_year"] - pop["deaths_year"],
                                 f"tick {snap['tick']}: population delta != births - deaths")
            prev_alive = pop["alive"]
        slice_ = world.state["population"]
        initial = sum(1 for p in slice_["people"].values() if p["generation"] == 0)
        self.assertEqual(slice_["alive"],
                         initial + slice_["births_total"] - slice_["deaths_total"])

    def test_by_generation_matches_recount(self):
        world, _ = run_world(1024, 40)
        maintained = dict(world.state["population"]["by_generation"])
        recounted = recount(world)
        self.assertEqual(maintained, recounted["by_generation"])
        self.assertEqual(world.state["population"]["alive"], recounted["alive"])


# === Engine seams ===
class TestEngineSeams(unittest.TestCase):
    def test_apply_death_bookkeeping(self):
        world, _ = run_world(1024, 5)
        slice_ = world.state["population"]
        victim = next(p for p in slice_["people"].values()
                      if p["alive"] and p["partner"] is not None)
        partner = slice_["people"][victim["partner"]]
        alive0, dt0, dy0 = slice_["alive"], slice_["deaths_total"], slice_["deaths_year"]
        self.assertTrue(apply_death(world, victim["pid"], "bridge_outbreak", incident_day=9))
        self.assertFalse(victim["alive"])
        self.assertIsNone(victim["partner"])
        self.assertIsNone(partner["partner"])
        self.assertEqual(victim["cause_of_death"], "bridge_outbreak")
        self.assertEqual(victim["died_incident_day"], 9)
        self.assertEqual(slice_["alive"], alive0 - 1)
        self.assertEqual(slice_["deaths_total"], dt0 + 1)
        self.assertEqual(slice_["deaths_year"], dy0 + 1)
        # Second kill is a no-op.
        self.assertFalse(apply_death(world, victim["pid"], "bridge_outbreak"))
        self.assertEqual(slice_["deaths_total"], dt0 + 1)
        # Counters stayed consistent with a full recount.
        maintained = dict(slice_["by_generation"])
        self.assertEqual(maintained, recount(world)["by_generation"])

    def test_apply_death_accepts_string_pids(self):
        # The bridge outbreak layer indexes people by str after save/load.
        world, _ = run_world(1024, 2)
        slice_ = world.state["population"]
        pid = next(p["pid"] for p in slice_["people"].values() if p["alive"])
        self.assertTrue(apply_death(world, str(pid), "bridge_outbreak"))
        self.assertFalse(slice_["people"][pid]["alive"])

    def test_trim_founders(self):
        world = build_world(make_config(1024, 10))
        orch = Orchestrator(build_registry(), world, dt_years=1.0)
        orch.initialize()
        slice_ = world.state["population"]
        n_before = len(slice_["people"])
        removed = trim_founders(world, 1000)
        self.assertEqual(removed, n_before - 1000)
        self.assertEqual(len(slice_["people"]), 1000)
        self.assertEqual(slice_["alive"], 1000)
        self.assertEqual(slice_["by_generation"], {"0": 1000})

    def test_event_archive_is_bounded(self):
        world, _ = run_world(1024, 40)
        bus = world.events
        self.assertLessEqual(len(bus.archive()), bus.ARCHIVE_TAIL)
        self.assertGreater(bus.archived_total(), bus.ARCHIVE_TAIL)


# === Life histories ===
def synthetic_people(n_founders: int, n_children: int) -> dict:
    people = {}
    for pid in range(1, n_founders + 1):
        people[pid] = {"pid": pid, "age": 40.0, "alive": True, "generation": 0,
                       "parents": None, "partner": None, "children": [],
                       "cause_of_death": None, "died_year": None}
    for i in range(n_children):
        pid = n_founders + 1 + i
        pa, pb = 2 * i + 1, 2 * i + 2
        people[pid] = {"pid": pid, "age": 20.0, "alive": True, "generation": 1,
                       "parents": (pa, pb), "partner": None, "children": [],
                       "cause_of_death": None, "died_year": None}
    return people


class TestLifeHistories(unittest.TestCase):
    def test_reproducible_across_books(self):
        people = synthetic_people(200, 90)
        b1 = LifeHistoryBook(12345, people)
        b2 = LifeHistoryBook(12345, dict(people))
        for pid in (1, 57, 200, 250):
            self.assertEqual(b1.profile(pid, 30), b2.profile(pid, 30))
        b3 = LifeHistoryBook(54321, people)
        self.assertNotEqual([b1.profile(p, 30) for p in range(1, 40)],
                            [b3.profile(p, 30) for p in range(1, 40)])

    def test_history_is_append_only_as_person_ages(self):
        # The Dwarf Fortress property: aging a year replays the same past.
        people = synthetic_people(50, 0)
        book = LifeHistoryBook(99, people)
        before = book.history(7, current_year=10)
        people[7]["age"] += 1.0
        after = book.history(7, current_year=11)
        self.assertEqual(before, after[:len(before)])

    def test_founder_trait_distributions(self):
        people = synthetic_people(4000, 0)
        book = LifeHistoryBook(2026, people)
        cols = {t: [] for t in (*BIG_FIVE, "gca")}
        for pid in people:
            tr = book.traits(pid)
            for k in cols:
                cols[k].append(tr[k])
        for t in BIG_FIVE:
            self.assertAlmostEqual(statistics.fmean(cols[t]), TRAIT_MEAN, delta=0.6)
            self.assertAlmostEqual(statistics.stdev(cols[t]), TRAIT_SD, delta=0.6)
        self.assertAlmostEqual(statistics.fmean(cols["gca"]), GCA_MEAN, delta=0.9)
        self.assertAlmostEqual(statistics.stdev(cols["gca"]), GCA_SD, delta=0.9)

    def test_heritability_regression_to_mean(self):
        # Child deviation should regress on midparent deviation with slope ≈ h²,
        # and population variance should stay stationary (Falconer & Mackay).
        n_children = 1900
        people = synthetic_people(2 * n_children, n_children)
        book = LifeHistoryBook(777, people)

        def slope_and_sd(key, mean):
            mids, kids = [], []
            for i in range(n_children):
                child = 2 * n_children + 1 + i
                pa, pb = people[child]["parents"]
                mids.append((book.traits(pa)[key] + book.traits(pb)[key]) / 2 - mean)
                kids.append(book.traits(child)[key] - mean)
            mm = statistics.fmean(mids)
            km = statistics.fmean(kids)
            cov = sum((a - mm) * (b - km) for a, b in zip(mids, kids)) / (len(mids) - 1)
            return cov / statistics.variance(mids), statistics.stdev(kids)

        slope_c, sd_c = slope_and_sd("conscientiousness", TRAIT_MEAN)
        self.assertAlmostEqual(slope_c, H2_PERSONALITY, delta=0.08)
        self.assertAlmostEqual(sd_c, TRAIT_SD, delta=1.0)
        slope_g, sd_g = slope_and_sd("gca", GCA_MEAN)
        self.assertAlmostEqual(slope_g, H2_GCA, delta=0.08)
        self.assertAlmostEqual(sd_g, GCA_SD, delta=1.5)

    def test_profile_shape_and_provenance(self):
        world, _ = run_world(1024, 12)
        people = world.state["population"]["people"]
        book = LifeHistoryBook(42, people)
        adult_pid = next(p["pid"] for p in people.values() if p["alive"] and p["age"] >= 30)
        prof = book.profile(adult_pid, world.tick)
        for key in ("traits", "trait_descriptors", "gca", "track", "grade",
                    "events", "provenance", "trait_scale", "gca_scale"):
            self.assertIn(key, prof)
        self.assertIn("fictional individuals", prof["provenance"])
        self.assertEqual(set(prof["traits"]), set(BIG_FIVE))
        # Dead person's biography freezes at the recorded death year.
        dead = next((p for p in people.values() if not p["alive"]), None)
        if dead is not None:
            dprof = book.profile(dead["pid"], world.tick)
            self.assertEqual(dprof["events"][-1]["kind"], "death")

    def test_trait_conditioned_career_rates(self):
        # Sensitivity: high-conscientiousness/high-GCA people should out-promote
        # and under-discipline low ones on average (Schmidt & Hunter / Barrick &
        # Mount weighting). Statistical, over many synthetic 40-year-olds.
        people = synthetic_people(3000, 0)
        book = LifeHistoryBook(31337, people)
        hi_p, lo_p, hi_d, lo_d = [], [], [], []
        for pid in people:
            tr = book.traits(pid)
            prof = book.profile(pid, 30)
            promos = sum(1 for e in prof["events"] if e["kind"] == "promotion")
            disc = prof["discipline_events"]
            score = (tr["conscientiousness"] - TRAIT_MEAN) / TRAIT_SD + (tr["gca"] - GCA_MEAN) / GCA_SD
            if score > 1.0:
                hi_p.append(promos)
                hi_d.append(disc)
            elif score < -1.0:
                lo_p.append(promos)
                lo_d.append(disc)
        self.assertGreater(statistics.fmean(hi_p), statistics.fmean(lo_p))
        self.assertLess(statistics.fmean(hi_d), statistics.fmean(lo_d))


if __name__ == "__main__":
    unittest.main()
