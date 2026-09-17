"""Offline council-retention regressions for Astra's compact planning context.

Only synthetic consultation records are used. No model, service, or save is called.
"""
import copy
import unittest

import engine
import live_voyage
import voyage


def council(identifier, group, people, person_id=None):
    return {"id": identifier, "group": group, "person_id": person_id, "people": people, "eligible_total": 1031 if group == "all" else 159, "sampled": people == 500, "sampling_note": "Deterministic fictional fixture.", "voice_label": "Astra-voiced fictional perspectives grounded in Jev", "revision": 4, "question": "Which tradeoff matters?", "mean_distribution": {"contain": .6, "care": .3, "conserve": .1}, "labels": {"contain": "Contain + care", "care": "Care + keep production", "conserve": "Contain + conserve medicine"}, "synthesis": identifier, "members": [], "positions": {}, "experts": []}


class CouncilContextTests(unittest.TestCase):
    def setUp(self):
        self.c = engine.Campaign(engine.make_config({"crew": 200, "ship": "ship_modular_cluster", "drive": "prop_fusion_continuous", "speed": .05, "launch_year": 2250}))
        voyage.ensure(self.c)

    def test_repeats_preserve_medical_full_council_and_latest_person_in_chronology(self):
        self.c.play["consultations"] = [
            council("medical-full", "medical", 159),
            council("person-old", "medical", 1, "7"),
            council("all-old", "all", 500),
            council("person-latest", "medical", 1, "7"),
            council("all-latest", "all", 500),
        ]
        before = copy.deepcopy(self.c.play)
        rows = live_voyage.context(self.c)["councils"]
        self.assertEqual([row["id"] for row in rows], ["medical-full", "person-latest", "all-latest"])
        self.assertEqual([row["people"] for row in rows], [159, 1, 500])
        self.assertEqual(rows[1]["person_id"], "7")
        self.assertTrue(rows[2]["sampled"])
        self.assertEqual(rows[2]["eligible_total"], 1031)
        self.assertTrue(all(row["voice_label"] for row in rows))
        self.assertTrue(all("members" not in row and "positions" not in row for row in rows))
        self.assertEqual(self.c.play, before)

    def test_distinct_people_survive_but_only_within_retained_six(self):
        self.c.play["consultations"] = [
            council("expired-engineering", "engineering", 140),
            council("medical-old", "medical", 159),
            council("person-seven", "medical", 1, "7"),
            council("all-old", "all", 500),
            council("person-nine", "medical", 1, "9"),
            council("medical-latest", "medical", 157),
            council("all-latest", "all", 500),
        ]
        rows = live_voyage.context(self.c)["councils"]
        self.assertEqual([row["id"] for row in rows], ["person-seven", "person-nine", "medical-latest", "all-latest"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
