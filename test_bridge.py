"""Offline bridge regression tests. No model requests and no production save edits."""
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import engine
import voyage
import live_voyage
from providers import parse_answer

OPTIONS = {"crew": 200, "ship": "ship_modular_cluster", "drive": "prop_fusion_continuous", "speed": .05, "launch_year": 2250}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.c = engine.Campaign(engine.make_config(OPTIONS))

    def test_founder_counts_and_distance(self):
        self.assertEqual(self.c.summary()["crew"], 200)
        self.assertEqual(self.c.summary()["arrival_year"], 85)
        options = {**OPTIONS, "crew": 1024, "ship": "ship_aurora_ark"}
        self.assertEqual(engine.Campaign(engine.make_config(options)).summary()["crew"], 1024)

    def test_optional_large_manifest_reaches_the_engine(self):
        options = {**OPTIONS, "crew": 50000, "ship": "ship_aurora_ark"}
        self.assertEqual(engine.make_config(options)["population"]["initial"], 50000)
        with self.assertRaises(ValueError):
            engine.make_config({**options, "crew": 50001})
        client = (engine.ROOT / "public/app.js").read_text()
        self.assertIn('max="50000"', client)
        self.assertIn("crew:1024", client)
        self.assertNotIn("Math.min(1200,ship.max_crew)", client)

    def test_draw_starts_fresh(self):
        c = engine.generate(OPTIONS)
        self.assertEqual(len(c.candidates), 6)
        self.assertIn(c.selected, range(6))
        self.assertEqual(c.world.tick, 0)
        self.assertEqual(c.summary()["births"], 0)
        self.assertEqual(c.summary()["crew"], 200)
        self.assertTrue(all(x["years"] == 8 for x in c.candidates))
        self.assertIn("trial_config", c.candidates[0])

    def test_invalid_action_and_years_are_noops(self):
        before = copy.deepcopy(self.c.world.state)
        for action, years in (("bogus", 1), ("gardens", "invalid"), ("gardens", 99)):
            with self.assertRaises(ValueError):
                self.c.advance(action, years, {})
            self.assertEqual(self.c.world.state, before)
            self.assertEqual(self.c.world.tick, 0)

    def test_probabilities_affect_morale(self):
        other = engine.Campaign(copy.deepcopy(self.c.config))
        support = {"1": {"probabilities": {"support": 1., "question": 0., "oppose": 0.}, "choice": "support"}}
        oppose = {"1": {"probabilities": {"support": 0., "question": 0., "oppose": 1.}, "choice": "oppose"}}
        self.c.advance("gardens", 1, support)
        other.advance("gardens", 1, oppose)
        self.assertGreater(self.c.summary()["morale"], other.summary()["morale"])

    def test_fractional_delay_not_truncated(self):
        self.c.config["mission"]["voyage_years"] = 2
        self.c.world.state["ship_integrity"]["voyage_extension_years"] = .9
        self.assertEqual(self.c.summary()["arrival_year"], 3)

    def test_reload_continues_exact_rng(self):
        self.c.advance("gardens", 3, {})
        with tempfile.TemporaryDirectory(prefix="proxima-test-") as temp:
            with patch.object(engine, "ROOT", Path(temp)):
                self.c.save()
                restored = engine.Campaign.load(Path(temp) / "state" / (self.c.id + ".json"))
            for _ in range(4):
                for c in (self.c, restored):
                    c.advance(engine.SITUATIONS[c.situation]["actions"][0], 3, {})
                self.assertEqual(json.dumps(self.c.world.state, sort_keys=True), json.dumps(restored.world.state, sort_keys=True))
                self.assertEqual(engine.rng_dump(self.c.world.rng), engine.rng_dump(restored.world.rng))

    def test_distribution_validation(self):
        values, _ = parse_answer({"probabilities": {"support": .7, "question": .2, "oppose": .1}, "confidence": .8})
        self.assertAlmostEqual(sum(values.values()), 1.)
        for probs in ({"support": 1}, {"support": float("nan"), "question": 0, "oppose": 1}, {"support": 0, "question": 0, "oppose": 0}):
            with self.assertRaises(RuntimeError):
                parse_answer({"probabilities": probs})

    def test_html_ids_match_client(self):
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__(); self.ids = []
            def handle_starttag(self, tag, attrs):
                self.ids.extend(v for k, v in attrs if k == "id")
        parser = Parser()
        parser.feed((engine.ROOT / "public/index.html").read_text())
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        referenced = set(re.findall(r"\$\('([^']+)'\)", (engine.ROOT / "public/app.js").read_text()))
        dynamic = set(re.findall(r'id="([a-z][\w-]+)"', (engine.ROOT / "public/app.js").read_text()))
        self.assertFalse(referenced - set(parser.ids) - dynamic)

    def test_v2_medicine_resolves_without_advancing_time(self):
        self.c.world.state["resources"]["medicine_kg"] = 0
        voyage.open_incident(self.c, "health")
        with patch.object(self.c, "save"):
            voyage.commit(self.c, "Replenish medical stores", {"operations": ["medical"]}, {}, {"summary": {}})
        self.assertEqual(self.c.world.tick, 0)
        self.assertEqual(self.c.summary()["medicine_years"], 6)
        self.assertEqual(self.c.play["mode"], "ready")
        self.assertEqual(self.c.play["revision"], 1)

    def test_v2_repair_clears_active_failure(self):
        self.c.world.state["ship_integrity"].update(integrity=.84, active_failure="engine_failure", active_years_remaining=4, active_severity=.02)
        voyage.open_incident(self.c, "repair")
        with patch.object(self.c, "save"):
            voyage.commit(self.c, "Repair the damaged machinery", {"operations": ["maintenance"]}, {}, {"summary": {}})
        self.assertTrue(self.c.play["incident"]["resolved"])
        self.assertIsNone(self.c.summary()["active_failure"])
        self.assertEqual(self.c.world.read("mortality.modifiers.system_failure"), 1)

    def test_v2_food_barrier_has_a_recovery_path(self):
        self.c.world.state["resources"]["food_kg"] = 200 * 1.2 * 30
        voyage.open_incident(self.c, "food")
        before = copy.deepcopy(self.c.world.state)
        with self.assertRaises(ValueError):
            voyage.validate_plan(self.c, {"operations": ["medical"]})
        self.assertEqual(self.c.world.state, before)
        self.assertEqual(voyage.validate_plan(self.c, {"operations": ["ration"]})["food_multiplier"], 1)

    def test_v2_invalid_plans_are_noops(self):
        voyage.open_incident(self.c, "health")
        before = copy.deepcopy(self.c.world.state)
        for plan in (None, [], {"operations": [{}]}, {"operations": ["medical", "medical"]}, {"operations": ["warp"]}):
            with self.assertRaises(ValueError):
                voyage.validate_plan(self.c, plan)
            self.assertEqual(before, self.c.world.state)

    def test_v2_accepting_risk_is_not_a_repair(self):
        self.c.world.state["resources"]["medicine_kg"] = 0
        voyage.open_incident(self.c, "health")
        with patch.object(self.c, "save"):
            voyage.commit(self.c, "Explicitly accept this risk", {"operations": ["hold"]}, {}, {"summary": {}})
        self.assertTrue(self.c.play["incident"]["accepted_risk"])
        self.assertEqual(self.c.summary()["medicine_years"], 0)

    def test_v2_narration_failure_preserves_committed_result(self):
        voyage.open_incident(self.c, "health")
        with patch.object(self.c, "save"), patch.object(live_voyage, "compile_order", return_value=({"operations": ["medical"], "unsupported": False}, {})), patch.object(live_voyage, "evaluate_people", return_value=({}, {"summary": {}})), patch.object(live_voyage, "astra", side_effect=RuntimeError("unavailable")):
            result = live_voyage.decide(self.c, "Replenish medicine", lambda _: None)
        self.assertTrue(result["committed"])
        self.assertIn("narration_error", result)
        self.assertEqual(self.c.play["revision"], 1)

    def test_v2_cryo_stops_at_actual_arrival(self):
        self.c.config["mission"]["voyage_years"] = 1
        voyage.ensure(self.c)
        frames = []
        with patch.object(self.c, "save"), patch.object(live_voyage, "watch", return_value=0), patch.object(live_voyage, "astra", return_value="Arrived."):
            live_voyage.cryo(self.c, frames.append)
        self.assertEqual(self.c.world.tick, 1)
        self.assertEqual(self.c.status, "arrived")
        self.assertEqual(self.c.play["mode"], "ended")

    def test_v2_delay_does_not_reverse_the_star_route(self):
        voyage.ensure(self.c)
        voyage.travel_tick(self.c)
        distance = self.c.play["distance_traveled_ly"]
        voyage.open_incident(self.c, "review")
        with patch.object(self.c, "save"):
            voyage.commit(self.c, "Delay arrival for repairs", {"operations": ["slow"]}, {}, {"summary": {}})
        self.assertEqual(self.c.play["distance_traveled_ly"], distance)
        voyage.travel_tick(self.c)
        self.assertEqual(self.c.play["distance_traveled_ly"], distance)
        self.assertGreater(self.c.summary()["arrival_year"], 85)


if __name__ == "__main__":
    unittest.main(verbosity=2)
