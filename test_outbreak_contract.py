"""Offline configuration/public-state checks for the captain outbreak challenge.

Owns boundary regression coverage, not disease tuning. No provider requests,
production save edits, or scripted casualty outcomes are used here.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
import outbreak


# === Catalog-derived journey and explicit scenario selection ===
OPTIONS = {
    "destination": "trappist_1_e", "drive": "prop_fusion_continuous",
    "ship": "ship_aurora_ark", "crew": 1024, "speed": .03,
    "launch_year": 2250, "scenario": "outbreak",
}


class OutbreakContractTests(unittest.TestCase):
    def campaign(self, **changes):
        return engine.Campaign(engine.make_config({**OPTIONS, **changes}))

    def test_long_journey_uses_catalog_distance_and_speed(self):
        c = self.campaign()
        self.assertEqual(c.config["mission"]["destination"], "trappist_1_e")
        self.assertEqual(c.config["mission"]["distance_ly"], 40.66)
        self.assertEqual(c.summary()["arrival_year"], 1356)
        self.assertEqual(c.summary()["crew"], 1024)
        self.assertEqual(c.config["scenario"], {"kind": "outbreak", "onset_year": 4, "version": 1})
        with self.assertRaisesRegex(ValueError, "1,500"):
            self.campaign(speed=.02)

    def test_scenario_selection_is_explicit_and_bounded(self):
        for choice in (None, "normal"):
            c = self.campaign(scenario=choice)
            self.assertNotIn("scenario", c.config)
            self.assertNotIn("bridge_outbreak", c.world.state)
            self.assertIsNone(c.public()["outbreak"])
        old_options = {k: v for k, v in OPTIONS.items() if k != "scenario"}
        self.assertNotIn("scenario", engine.make_config(old_options))
        for invalid in ("", "unknown", {"kind": "outbreak"}, False, 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                engine.make_config({**OPTIONS, "scenario": invalid})

    def test_draw_does_not_run_outbreak_in_commissioning_trials(self):
        # A common seed makes the comparison exact without predetermining normal
        # gameplay. generate() still calls fresh randomness for the actual voyage.
        with patch.object(engine.secrets, "randbits", return_value=70019), patch.object(engine.secrets, "randbelow", return_value=3):
            normal = engine.generate({**OPTIONS, "scenario": "normal"})
            challenge = engine.generate(OPTIONS)
        trial_metrics = lambda c: [{k: v for k, v in row.items() if k != "trial_config"} for row in c.candidates]
        self.assertEqual(trial_metrics(normal), trial_metrics(challenge))
        self.assertEqual(len(challenge.candidates), 6)
        self.assertEqual(challenge.world.tick, 0)
        self.assertEqual(challenge.summary()["crew"], 1024)
        self.assertEqual(challenge.summary()["births"], 0)
        self.assertEqual(challenge.public()["outbreak"]["status"], "armed")
        self.assertEqual(challenge.public()["outbreak"]["infected"], 0)

    # === Compact public data, player role, and actual population identity ===
    def test_health_and_casualties_follow_real_person_ids(self):
        c = self.campaign()
        before = {row["id"]: row for row in c.crew()}
        people = c.world.state["population"]["people"]
        pids = list(people)[:4]
        people[pids[0]]["alive"] = False
        people[pids[0]]["cause_of_death"] = "bridge_outbreak"
        people[pids[3]]["alive"] = False  # unrelated annual-engine casualty
        people[pids[3]]["cause_of_death"] = "natural"
        health = {
            str(pids[0]): {"status": "dead"},
            str(pids[1]): {"status": "critical"},
            str(pids[2]): {"status": "recovered"},
            str(pids[3]): {"status": "dead"},
        }
        with patch.object(outbreak, "health", return_value=health):
            live = {row["id"]: row for row in c.crew()}
            dead = c.casualties()
        self.assertNotIn(str(pids[0]), live)
        self.assertNotIn(str(pids[3]), live)
        self.assertEqual(live[str(pids[1])]["health"], "critical")
        self.assertEqual(live[str(pids[2])]["health"], "recovered")
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0]["id"], str(pids[0]))
        self.assertEqual(dead[0]["health"], "dead")
        for key in ("room", "age", "generation", "role"):
            self.assertEqual(dead[0][key], before[str(pids[0])][key])

    def test_public_data_omits_private_outbreak_and_model_audits(self):
        c = self.campaign()
        c.world.state["bridge_outbreak"]["private_contract_test"] = "PRIVATE_DISEASE_SENTINEL"
        c.jev_runs.append({"summary": {"people": 1024}, "request": "PRIVATE_JEV_SENTINEL"})
        c.astra_runs.append({"request": "PRIVATE_ASTRA_SENTINEL"})
        snapshot = copy.deepcopy(c.world.state)
        public = c.public()
        serialized = json.dumps(public)
        for sentinel in ("PRIVATE_DISEASE_SENTINEL", "PRIVATE_JEV_SENTINEL", "PRIVATE_ASTRA_SENTINEL"):
            self.assertNotIn(sentinel, serialized)
        self.assertEqual(c.world.state, snapshot)
        self.assertNotIn("people", public["outbreak"])
        self.assertNotIn("health", public["outbreak"])
        context = c.context()
        self.assertNotIn("crew", context)
        self.assertNotIn("casualties", context)
        self.assertEqual(context["outbreak"], public["outbreak"])

    def test_captain_is_player_role_and_has_legacy_defaults(self):
        c = self.campaign(scenario="normal")
        self.assertEqual(c.public()["captain"], {"role": "You are the admiral", "state": "awake", "cycles": 0, "awake_days": 0, "slept_years": 0})
        c.play = {"mode": "cryo", "captain_cycles": 2, "captain_awake_days": 28, "captain_slept_years": 4}
        captain = c.public()["captain"]
        self.assertEqual(captain["state"], "cryogenesis")
        self.assertEqual(captain["cycles"], 2)
        self.assertEqual(captain["awake_days"], 28)
        self.assertEqual(captain["slept_years"], 4)
        self.assertEqual(len(c.crew()), 1024)
        self.assertTrue(all(row["health"] == "susceptible" for row in c.crew()))

    def test_save_load_keeps_armed_scenario_without_starting_it(self):
        c = self.campaign()
        with tempfile.TemporaryDirectory(prefix="proxima-outbreak-contract-") as temp:
            with patch.object(engine, "ROOT", Path(temp)):
                c.save()
                restored = engine.Campaign.load(Path(temp) / "state" / (c.id + ".json"))
        self.assertEqual(restored.config, c.config)
        self.assertEqual(restored.public()["outbreak"], c.public()["outbreak"])
        self.assertEqual(restored.world.state, c.world.state)
        self.assertEqual(engine.rng_dump(restored.world.rng), engine.rng_dump(c.world.rng))


if __name__ == "__main__":
    unittest.main(verbosity=2)
