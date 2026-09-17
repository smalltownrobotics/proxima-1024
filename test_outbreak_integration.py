"""Offline boundary tests for outbreak orders, cryo recovery and private audits.

The fixtures supply explicit synthetic Jev probabilities; no provider, browser or
running service is contacted. Saves and reloads use temporary directories only.
"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
import live_voyage
import outbreak
import voyage

# engine prepends ../sim to sys.path; a plain `import server` would silently load
# the legacy simulator's server. Bind the exact bridge entry point under test.
_server_spec = importlib.util.spec_from_file_location("bridge_server_under_test", Path(__file__).with_name("server.py"))
server = importlib.util.module_from_spec(_server_spec)
_server_spec.loader.exec_module(server)


# === Reproducible campaign and complete synthetic response fixtures ===
OPTIONS = {"crew": 1024, "ship": "ship_aurora_ark", "drive": "prop_fusion_continuous", "destination": "trappist_1_e", "speed": .03, "launch_year": 2250, "scenario": "outbreak"}


def new_campaign(seed=0, awake=True):
    config = engine.make_config(OPTIONS)
    config["tunables"]["rng_seed"] = seed
    c = engine.Campaign(config)
    voyage.ensure(c)
    if awake:
        c.world.tick = 4
        outbreak.start_if_due(c)
        voyage.open_incident(c, "outbreak")
    return c


def reactions(c):
    return {p["id"]: {"probabilities": {"support": .8, "question": .15, "oppose": .05}, "choice": "support", "confidence": .9} for p in c.crew()}


def audit(c):
    return {"summary": {"model": "offline-test-fixture", "people": len(c.crew()), "seconds": 0}, "batches": [{"private_fixture_marker": "NOT_PUBLIC"}]}


def commit(c, ops):
    with patch.object(c, "save"):
        return voyage.commit(c, "An explicit test order.", {"operations": ops, "interpretation": "Test policy"}, reactions(c), audit(c))


class OutbreakIntegrationTests(unittest.TestCase):
    # === Physical commit, resource ledger and irreversible casualties ===
    def test_commit_has_real_casualties_and_exact_single_resource_charge(self):
        c = new_campaign()
        before = copy.deepcopy(c.world.state["resources"])
        before_population = c.summary()["crew"]
        record = commit(c, ["isolate", "surge_care"])
        physical = record["outbreak_result"]
        self.assertGreater(physical["after"]["deaths"], 0)
        self.assertEqual(c.summary()["crew"], before_population - physical["after"]["deaths"])
        self.assertEqual(len(c.casualties()), physical["after"]["deaths"])
        self.assertEqual(c.world.read("population.alive"), sum(p["alive"] for p in c.world.state["population"]["people"].values()))
        self.assertAlmostEqual(before["food_kg"] - c.world.read("resources.food_kg"), physical["food_spent_kg"], delta=.006)
        self.assertAlmostEqual(before["medicine_kg"] - c.world.read("resources.medicine_kg"), physical["medicine_spent_kg"], delta=.006)
        self.assertEqual(record["plan"]["food_multiplier"], 1)
        self.assertEqual(record["plan"]["work_cost"], 80)
        self.assertEqual(c.play["revision"], 1)
        self.assertEqual(c.play["captain_awake_days"], 28)
        self.assertEqual(len(record["outbreak_frames"]), 28)
        self.assertEqual(c.play["last_decision"]["outbreak_result"], physical)

    def test_hold_advances_disease_without_falsely_resolving_it(self):
        c = new_campaign()
        record = commit(c, ["hold"])
        self.assertEqual(record["outbreak_result"]["days"], 28)
        self.assertGreater(outbreak.summary(c)["infected"], 0)
        self.assertGreater(outbreak.summary(c)["deaths"], 0)
        self.assertFalse(c.play["incident"]["resolved"])
        self.assertFalse(c.play["incident"]["accepted_risk"])
        self.assertEqual(c.play["mode"], "awake")
        self.assertEqual(c.play["work"], 100)

    def test_cryo_blocks_active_disease_even_if_mode_was_ready(self):
        c = new_campaign()
        for mode in ("awake", "ready"):
            c.play["mode"] = mode
            before = copy.deepcopy((c.world.state, c.play))
            with patch.object(live_voyage, "watch", side_effect=AssertionError("A provider must not be called")):
                with self.assertRaises(ValueError):
                    live_voyage.cryo(c, lambda _: None)
            self.assertEqual((c.world.state, c.play), before)

    def test_incomplete_jev_cannot_partially_commit(self):
        c = new_campaign()
        before = copy.deepcopy((c.world.state, c.play, c.history, c.jev_runs, engine.rng_dump(c.world.rng)))
        with patch.object(c, "save"), self.assertRaises(ValueError):
            voyage.commit(c, "Isolate and care.", {"operations": ["isolate", "surge_care"]}, {}, audit(c))
        self.assertEqual((c.world.state, c.play, c.history, c.jev_runs, engine.rng_dump(c.world.rng)), before)

    # === Live orchestration failures do not undo or duplicate an order ===
    def test_postcommit_narration_failure_keeps_frames_and_single_revision(self):
        c = new_campaign()
        plan = {"operations": ["isolate", "surge_care"], "unsupported": False}
        response, model_audit = reactions(c), audit(c)
        pulses = []
        with tempfile.TemporaryDirectory(prefix="proxima-outbreak-integration-") as temp:
            with patch.object(engine, "ROOT", Path(temp)), patch.object(live_voyage, "compile_order", return_value=(plan, {})), patch.object(live_voyage, "evaluate_people", return_value=(response, model_audit)), patch.object(live_voyage, "astra", side_effect=RuntimeError("Narration unavailable")), patch("advisory.assess", return_value={"summary": {"priority": "deliberate"}, "audit": {}}):
                result = live_voyage.decide(c, "Isolate and use emergency care.", pulses.append)
            saved = engine.Campaign.load(Path(temp) / "state" / f"{c.id}.json")
        self.assertTrue(result["committed"])
        self.assertIn("narration_error", result)
        self.assertEqual(len(result["outbreak_frames"]), 28)
        self.assertEqual(pulses[-1]["outbreak_frames"], result["outbreak_frames"])
        self.assertEqual(result["campaign"]["outbreak"], outbreak.summary(saved))
        self.assertEqual((saved.play["revision"], len(saved.history), len(saved.jev_runs)), (1, 1, 1))

    def test_failed_watch_at_onset_leaves_actionable_awake_incident(self):
        c = new_campaign(awake=False)
        for _ in range(3):
            voyage.travel_tick(c)
        with patch.object(c, "save"), patch.object(live_voyage, "watch", side_effect=RuntimeError("Watch unavailable")), self.assertRaises(RuntimeError):
            live_voyage.cryo(c, lambda _: None)
        self.assertEqual(c.world.tick, 4)
        self.assertTrue(outbreak.summary(c)["active"])
        self.assertEqual(c.play["mode"], "awake", "A network failure must not strand an active outbreak in ready mode")
        self.assertEqual(c.play["incident"]["kind"], "outbreak")

    def test_process_reload_at_onset_recovers_actionable_incident(self):
        c = new_campaign(awake=False)
        for _ in range(3):
            voyage.travel_tick(c)
        c.play["mode"] = "cryo"
        voyage.travel_tick(c)  # Actual persisted onset, before the network watch.
        with tempfile.TemporaryDirectory(prefix="proxima-outbreak-recovery-") as temp:
            with patch.object(engine, "ROOT", Path(temp)):
                c.save()
            with patch.object(server, "ROOT", Path(temp)), patch.object(server, "CAMPAIGNS", {}):
                restored = server.get_campaign(c.id)
        self.assertTrue(outbreak.summary(restored)["active"])
        self.assertEqual(restored.play["mode"], "awake")
        self.assertEqual(restored.play["incident"]["kind"], "outbreak")

    # === Saved-state reproducibility and finite policy consequences ===
    def test_equal_initial_rng_different_policy_has_different_physical_outcome(self):
        prompt, delay = new_campaign(), new_campaign()
        self.assertEqual(prompt.world.state, delay.world.state)
        self.assertEqual(engine.rng_dump(prompt.world.rng), engine.rng_dump(delay.world.rng))
        a, b = commit(prompt, ["isolate", "surge_care"]), commit(delay, ["hold"])
        self.assertLess(a["outbreak_result"]["after"]["deaths"], b["outbreak_result"]["after"]["deaths"])
        self.assertLess(a["outbreak_result"]["after"]["infected"], b["outbreak_result"]["after"]["infected"])
        self.assertNotEqual(prompt.world.state, delay.world.state)

    def test_reload_continues_outbreak_and_old_normal_voyage(self):
        c = new_campaign()
        commit(c, ["isolate", "surge_care"])
        with tempfile.TemporaryDirectory(prefix="proxima-outbreak-continuation-") as temp:
            with patch.object(engine, "ROOT", Path(temp)):
                c.save()
                restored = engine.Campaign.load(Path(temp) / "state" / f"{c.id}.json")
                normal = engine.Campaign(engine.make_config({**OPTIONS, "scenario": "normal"}))
                normal.save()
                old = engine.Campaign.load(Path(temp) / "state" / f"{normal.id}.json")
        self.assertIsNone(outbreak.summary(old))
        voyage.ensure(old)
        self.assertEqual(old.play["mode"], "ready")
        self.assertEqual(old.public()["casualties"], [])
        for obj in (c, restored):
            commit(obj, ["isolate"])
        self.assertEqual(c.world.state, restored.world.state)
        self.assertEqual(engine.rng_dump(c.world.rng), engine.rng_dump(restored.world.rng))
        self.assertTrue(c.play["incident"]["resolved"])
        self.assertEqual(c.play["mode"], "ready")
        self.assertEqual(c.play["revision"], 2)
        self.assertEqual(c.public()["casualties"], restored.public()["casualties"])

    def test_public_contract_excludes_all_private_provider_audits(self):
        c = new_campaign()
        c.play["advisory_audits"] = [{"request": {"private_audit_marker": "KEEP_PRIVATE"}, "response": {"raw_provider_output": "KEEP_PRIVATE"}}]
        c.play["blocked_runs"] = [{"request": {"private_audit_marker": "KEEP_PRIVATE"}}]
        c.play["wake_checks"] = [{"year": 4, "wake_probability": .8, "request": {"private_audit_marker": "KEEP_PRIVATE"}, "response": {}}]
        public = c.public()
        self.assertFalse("KEEP_PRIVATE" in json.dumps(public), "Raw provider audits escaped into the public campaign payload")
        self.assertNotIn("advisory_audits", public["play"])
        self.assertEqual(public["play"]["wake_checks"], [{"year": 4, "wake_probability": .8}])
        self.assertNotIn("cases", public["outbreak"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
