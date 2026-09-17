"""Offline outbreak mechanics tests: real roster transitions, no live model calls.

Synthetic distributions below are fixtures, never substitute demo Jev output.
Saves use a temporary directory and existing user campaigns are never loaded.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
import outbreak


# === Deterministic fictional fixtures ===
OPTIONS = {"crew": 1024, "ship": "ship_aurora_ark", "drive": "prop_fusion_continuous", "destination": "trappist_1_e", "speed": .03, "launch_year": 2250}


def campaign(seed=0, start=True):
    config = engine.make_config(OPTIONS)
    config["tunables"]["rng_seed"] = seed
    c = engine.Campaign(config)
    outbreak.arm(c, 4)
    if start:
        c.world.tick = 4
        outbreak.start_if_due(c)
    return c


def responses(c, probabilities=(.8, .15, .05)):
    return {str(pid): {"probabilities": dict(zip(("support", "question", "oppose"), probabilities)), "choice": "support"} for pid, person in c.world.state["population"]["people"].items() if person["alive"]}


def snapshot(c):
    return copy.deepcopy((c.world.state, engine.rng_dump(c.world.rng), c.status))


class OutbreakTests(unittest.TestCase):
    # === Opt-in, lifecycle and public/private boundary ===
    def test_legacy_save_stays_opted_out(self):
        c = engine.Campaign(engine.make_config(OPTIONS))
        before = snapshot(c)
        self.assertIsNone(outbreak.ensure(c))
        self.assertIsNone(outbreak.summary(c))
        self.assertEqual(outbreak.health(c), {})
        self.assertFalse(outbreak.start_if_due(c))
        self.assertEqual(snapshot(c), before)

    def test_explicit_onset_once_and_public_reads_are_pure(self):
        c = campaign(start=False)
        self.assertEqual(outbreak.summary(c)["status"], "armed")
        before = snapshot(c)
        for _ in range(4):
            outbreak.summary(c)
            outbreak.health(c)
        self.assertEqual(snapshot(c), before)
        c.world.tick = 3
        self.assertFalse(outbreak.start_if_due(c))
        c.world.tick = 4
        self.assertTrue(outbreak.start_if_due(c))
        self.assertEqual(outbreak.summary(c)["infected"], round(1024 * .065))
        before = snapshot(c)
        self.assertFalse(outbreak.start_if_due(c))
        self.assertEqual(snapshot(c), before)
        self.assertNotIn("cases", outbreak.summary(c))
        self.assertNotIn("duration", json.dumps(outbreak.health(c)))

    def test_zero_population_creates_no_cases(self):
        c = campaign(start=False)
        for person in c.world.state["population"]["people"].values():
            person["alive"] = False
        c.world.state["population"]["alive"] = 0
        c.world.tick = 4
        self.assertFalse(outbreak.start_if_due(c))
        s = outbreak.summary(c)
        self.assertEqual((s["status"], s["infected"], s["population"]), ("extinct", 0, 0))
        before = snapshot(c)
        with self.assertRaises(ValueError):
            outbreak.apply_response(c, ["hold"], {})
        self.assertEqual(snapshot(c), before)

    # === Atomic validation and finite stocks ===
    def test_invalid_plans_are_complete_noops(self):
        c = campaign()
        before = snapshot(c)
        for ops in (None, [], ["medical"], ["isolate", "isolate"], ["isolate", "protect_food"], ["hold", "surge_care"], [{}]):
            with self.subTest(ops=ops), self.assertRaises(ValueError):
                outbreak.apply_response(c, ops, responses(c))
            self.assertEqual(snapshot(c), before)

    def test_missing_or_invalid_jev_is_complete_noop(self):
        c = campaign()
        good = responses(c)
        bad = copy.deepcopy(good)
        bad[next(iter(bad))]["probabilities"]["support"] = float("nan")
        before = snapshot(c)
        for response in ({}, {next(iter(good)): next(iter(good.values()))}, bad):
            with self.assertRaises(ValueError):
                outbreak.apply_response(c, ["isolate", "surge_care"], response)
            self.assertEqual(snapshot(c), before)

    def test_protected_food_and_empty_medicine_block_before_mutation(self):
        c = campaign()
        c.world.state["resources"]["food_kg"] = 1024 * 1.2 * 50
        before = snapshot(c)
        with self.assertRaisesRegex(ValueError, "45-day"):
            outbreak.apply_response(c, ["isolate"], responses(c))
        self.assertEqual(snapshot(c), before)
        c.world.state["resources"]["medicine_kg"] = 0
        before = snapshot(c)
        with self.assertRaisesRegex(ValueError, "medicine"):
            outbreak.apply_response(c, ["surge_care"], responses(c))
        self.assertEqual(snapshot(c), before)
        self.assertEqual(outbreak.validate(c, ["hold"])["food_cost_max_kg"], 0)

    def test_care_can_run_out_without_inventing_supplies(self):
        c = campaign()
        resources = c.world.state["resources"]
        resources["medicine_kg"] = 1024 * outbreak.MEDICINE_SETUP_PER_PERSON + 1
        initial = resources["medicine_kg"]
        record = outbreak.apply_response(c, ["surge_care"], responses(c))
        self.assertGreaterEqual(resources["medicine_kg"], 0)
        self.assertLess(record["medicine_spent_kg"], initial + .01)
        self.assertEqual(record["frames"][-1]["care_delivered"], 0)
        self.assertLess(resources["medicine_kg"], outbreak.MEDICINE_PER_CARE_DAY)

    # === Roster conservation, per-day evidence and seeded continuation ===
    def test_real_casualties_and_population_conservation(self):
        c = campaign()
        pop = c.world.state["population"]
        initial_ids = set(pop["people"])
        before_alive = pop["alive"]
        before_deaths = pop["deaths_total"]
        record = outbreak.apply_response(c, ["hold"], responses(c))
        self.assertGreater(len(record["casualties"]), 0)
        dead = [p for p in pop["people"].values() if p["cause_of_death"] == "bridge_outbreak"]
        self.assertEqual(set(record["casualties"]), {str(p["pid"]) for p in dead})
        self.assertEqual(set(pop["people"]), initial_ids)
        self.assertEqual(before_alive - pop["alive"], len(dead))
        self.assertEqual(pop["deaths_total"] - before_deaths, len(dead))
        self.assertEqual(pop["alive"], sum(bool(p["alive"]) for p in pop["people"].values()))
        self.assertEqual(sum(pop["by_generation"].values()), pop["alive"])
        self.assertTrue(all(not p["alive"] and p["partner"] is None for p in dead))
        self.assertEqual(sum(f["new_deaths"] for f in record["frames"]), len(dead))
        self.assertEqual(record["after"]["deaths"], len(dead))
        self.assertEqual(len(record["frames"]), 28)
        self.assertEqual([f["day"] for f in record["frames"]], list(range(1, 29)))
        for frame in record["frames"]:
            states = list(frame["health"].values())
            self.assertEqual(sum(x["status"] != "dead" for x in states), frame["population"])
            self.assertEqual(sum(x["status"] in {"infected", "critical"} for x in states), frame["summary"]["infected"])
            self.assertEqual(sum(x["status"] == "dead" for x in states), frame["summary"]["deaths"])

    def test_death_releases_partner_and_cannot_double_count(self):
        c = campaign()
        people = c.world.state["population"]["people"]
        a, b = people[1], people[2]
        a["partner"], b["partner"] = 2, 1
        case = {"status": "infected", "day_resolved": None}
        before = c.world.read("population.deaths_total")
        self.assertTrue(outbreak._die(c, "1", a, case, 1))
        self.assertFalse(outbreak._die(c, "1", a, case, 2))
        self.assertIsNone(b["partner"])
        self.assertEqual(c.world.read("population.deaths_total"), before + 1)
        self.assertEqual(a["died_incident_day"], 1)

    def test_same_seed_exact_reproduction_and_save_reload_continuation(self):
        c, other = campaign(0), campaign(0)
        for obj in (c, other):
            outbreak.apply_response(obj, ["isolate", "surge_care"], responses(obj))
        self.assertEqual(snapshot(c), snapshot(other))
        self.assertTrue(outbreak.summary(c)["active"])
        with tempfile.TemporaryDirectory(prefix="proxima-outbreak-test-") as temp:
            with patch.object(engine, "ROOT", Path(temp)):
                c.save()
                restored = engine.Campaign.load(Path(temp) / "state" / f"{c.id}.json")
        for obj in (c, restored):
            outbreak.apply_response(obj, ["isolate"], responses(obj))
        self.assertEqual(snapshot(c), snapshot(restored))
        self.assertTrue(outbreak.summary(c)["resolved"])
        self.assertEqual(outbreak.summary(c)["day"], 56)
        self.assertEqual(c.world.tick, 4)  # nested tactical clock, not a hidden year jump
        before = snapshot(c)
        with self.assertRaises(ValueError):
            outbreak.apply_response(c, ["hold"], responses(c))
        self.assertEqual(snapshot(c), before)

    # === Policy meaning and full-distribution consequences ===
    def test_full_distribution_changes_execution_even_with_same_top_label(self):
        strong, hesitant = campaign(), campaign()
        a = outbreak.apply_response(strong, ["isolate", "surge_care"], responses(strong, (.9, .06, .04)))
        b = outbreak.apply_response(hesitant, ["isolate", "surge_care"], responses(hesitant, (.4, .35, .25)))
        self.assertGreater(a["compliance"]["mean"], b["compliance"]["mean"])
        self.assertGreater(a["after"]["care_capacity"], b["after"]["care_capacity"])
        self.assertNotEqual(a["frames"], b["frames"])

    def test_alternative_policy_changes_life_and_death_over_seed_set(self):
        treated_deaths = untreated_deaths = treated_cases = untreated_cases = 0
        for seed in range(6):
            protected, open_ship = campaign(seed), campaign(seed)
            a = outbreak.apply_response(protected, ["isolate", "surge_care"], responses(protected))
            b = outbreak.apply_response(open_ship, ["protect_food"], responses(open_ship))
            treated_deaths += a["after"]["deaths"]
            untreated_deaths += b["after"]["deaths"]
            treated_cases += a["after"]["ever_infected"]
            untreated_cases += b["after"]["ever_infected"]
            self.assertGreater(a["food_spent_kg"], b["food_spent_kg"])
            self.assertGreater(a["medicine_spent_kg"], b["medicine_spent_kg"])
        self.assertLess(treated_deaths, untreated_deaths)
        self.assertLess(treated_cases, untreated_cases)


if __name__ == "__main__":
    unittest.main(verbosity=2)
