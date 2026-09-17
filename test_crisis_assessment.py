"""Offline post-mutation assessment tests; no provider calls or production saves.

The real outbreak commit runs against an in-memory seeded world. Only assessment,
order interpretation, reaction distributions and narration are provider fixtures.
"""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import advisory
import engine
import live_voyage
import outbreak
import voyage


# === Real engine fixtures and deliberately distinct advisory readings ===
OPTIONS = {"destination": "trappist_1_e", "drive": "prop_fusion_continuous", "ship": "ship_aurora_ark", "crew": 1024, "speed": .03, "launch_year": 2250, "scenario": "outbreak"}


def campaign(awake=True, scenario="outbreak"):
    config = engine.make_config({**OPTIONS, "scenario": scenario})
    config["tunables"]["rng_seed"] = 29
    c = engine.Campaign(config)
    voyage.ensure(c)
    if awake:
        c.world.tick = 4
        outbreak.start_if_due(c)
        voyage.open_incident(c, "outbreak" if scenario == "outbreak" else "health")
    return c


def reactions(c):
    return {person["id"]: {"probabilities": {"support": .8, "question": .15, "oppose": .05}, "choice": "support"} for person in c.crew()}


def report():
    return {"summary": {"priority": "deliberate", "urgency": {"choice": "deliberate", "probabilities": {"routine": .1, "deliberate": .8, "immediate": .1}}, "read_only": True}, "audit": {"request": {"PRIVATE_ASSESSMENT_MARKER": True}, "response": {"model": "test-only"}}}


def decide(c, assessor, operations=None, unsupported=False):
    plan = {"operations": operations or ["isolate", "surge_care"], "unsupported": unsupported, "reason": "Unsupported fixture."}
    audit = {"summary": {"people": len(c.crew()), "model": "offline-fixture"}}
    frames = []
    with patch.object(live_voyage, "compile_order", return_value=(plan, {})), patch.object(live_voyage, "evaluate_people", return_value=(reactions(c), audit)), patch.object(live_voyage, "astra", return_value="Actual consequences recorded."), patch.object(advisory, "assess", side_effect=assessor) as request:
        result = live_voyage.decide(c, "Isolate affected decks and fund emergency care.", frames.append)
    return result, frames, request


class CrisisAssessmentTests(unittest.TestCase):
    def test_postcommit_assessment_reads_day28_and_new_revision(self):
        c = campaign()
        c.play["crisis_assessment"] = {"assessment_version": 1, "revision": 0, "year": 4, "day": 0, "priority": "immediate"}
        observed = []
        def assess(actual):
            observed.append((actual.play["revision"], outbreak.summary(actual)["day"], actual.summary()["deaths"], len(actual.history)))
            return report()
        with patch.object(c, "save"):
            result, pulses, request = decide(c, assess)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(observed, [(1, 28, c.summary()["deaths"], 1)])
        reading = result["campaign"]["play"]["crisis_assessment"]
        self.assertEqual((reading["revision"], reading["day"], reading["priority"]), (1, 28, "deliberate"))
        self.assertEqual(pulses[-1]["campaign"]["play"]["crisis_assessment"], reading)
        self.assertTrue(result["committed"])
        self.assertEqual(len(c.history), 1)
        self.assertEqual(c.play["advisory_audits"][-1]["trigger"], "outbreak_commit")
        self.assertNotIn("advisory_audits", result["campaign"]["play"])

    def test_failed_assessment_preserves_committed_result_and_saved_unavailable_stamp(self):
        c = campaign()
        c.play["crisis_assessment"] = {"revision": 0, "priority": "immediate", "urgency": {"old": True}}
        with tempfile.TemporaryDirectory(prefix="proxima-assessment-failure-") as temp:
            with patch.object(engine, "ROOT", Path(temp)):
                result, pulses, request = decide(c, RuntimeError("Provider unavailable"))
            saved = engine.Campaign.load(Path(temp) / "state" / f"{c.id}.json")
        self.assertEqual(request.call_count, 1)
        self.assertTrue(result["committed"])
        self.assertEqual(len(result["outbreak_frames"]), 28)
        self.assertEqual((saved.play["revision"], len(saved.history), len(saved.jev_runs)), (1, 1, 1))
        self.assertEqual(saved.world.state, c.world.state)
        reading = saved.play["crisis_assessment"]
        self.assertEqual((reading["revision"], reading["day"]), (1, 28))
        self.assertIn("unavailable", reading)
        self.assertNotIn("urgency", reading)
        self.assertNotIn("priority", reading)
        self.assertEqual(pulses[-1]["campaign"]["outbreak"], outbreak.summary(saved))
        self.assertEqual(saved.play["advisory_audits"][-1]["status"], "unavailable")

    def test_wake_reuses_helper_on_actual_onset(self):
        c = campaign(awake=False)
        for _ in range(3):
            voyage.travel_tick(c)
        observed = []
        def assess(actual):
            observed.append((actual.world.tick, actual.play["revision"], outbreak.summary(actual)["day"], actual.play["mode"]))
            return report()
        with patch.object(c, "save"), patch.object(live_voyage, "watch", return_value=0), patch.object(live_voyage, "astra", return_value="Awake."), patch.object(advisory, "assess", side_effect=assess):
            result = live_voyage.cryo(c, lambda _: None)
        self.assertEqual(observed, [(4, 4, 0, "awake")])
        self.assertEqual(result["campaign"]["play"]["crisis_assessment"]["revision"], 4)
        self.assertEqual(c.play["advisory_audits"][-1]["trigger"], "wake")

    def test_failed_assessment_does_not_erase_real_wake(self):
        c = campaign(awake=False)
        for _ in range(3):
            voyage.travel_tick(c)
        with patch.object(c, "save"), patch.object(live_voyage, "watch", return_value=0), patch.object(live_voyage, "astra", return_value="Awake."), patch.object(advisory, "assess", side_effect=RuntimeError("Unavailable")):
            result = live_voyage.cryo(c, lambda _: None)
        self.assertEqual(c.play["mode"], "awake")
        self.assertEqual(c.play["incident"]["kind"], "outbreak")
        self.assertTrue(outbreak.summary(c)["active"])
        reading = result["campaign"]["play"]["crisis_assessment"]
        self.assertEqual((reading["revision"], reading["day"]), (4, 0))
        self.assertIn("unavailable", reading)

    def test_unchanged_state_does_not_reissue_assessment_even_after_failure(self):
        for outcome in (report(), RuntimeError("Unavailable")):
            c = campaign()
            world, rng, revision = copy.deepcopy(c.world.state), engine.rng_dump(c.world.rng), c.play["revision"]
            with patch.object(c, "save"), patch.object(advisory, "assess", side_effect=outcome if isinstance(outcome, Exception) else lambda _: outcome) as request:
                first = live_voyage.refresh_crisis_assessment(c, "wake")
                second = live_voyage.refresh_crisis_assessment(c, "wake")
            self.assertEqual(request.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual((c.world.state, engine.rng_dump(c.world.rng), c.play["revision"]), (world, rng, revision))
            self.assertEqual(len(c.play["advisory_audits"]), 1)

    def test_blocked_preview_does_not_assess_unexecuted_outcome(self):
        c = campaign()
        before = copy.deepcopy(c.world.state)
        with patch.object(c, "save"):
            result, _, request = decide(c, AssertionError("No speculative assessment"), unsupported=True)
        request.assert_not_called()
        self.assertTrue(result["blocked"])
        self.assertEqual(c.world.state, before)
        self.assertEqual(c.play["revision"], 0)

    def test_regular_nonoutbreak_order_does_not_add_assessment_call(self):
        c = campaign(scenario="normal")
        with patch.object(c, "save"):
            result, _, request = decide(c, AssertionError("Not an outbreak commit"), operations=["medical"])
        request.assert_not_called()
        self.assertTrue(result["committed"])
        self.assertIsNone(result["campaign"]["outbreak"])

    def test_reactor_or_medical_reserve_incident_does_not_receive_disease_axes(self):
        for kind in ("repair", "health"):
            c = campaign(scenario="normal")
            voyage.open_incident(c, kind)
            c.play["crisis_assessment"] = {"revision": -1, "priority": "immediate", "pressure": {"choice": "transmission"}}
            before = copy.deepcopy(c.world.state)
            with patch.object(c, "save"), patch.object(advisory, "assess", side_effect=AssertionError("Not an outbreak")) as request:
                result = live_voyage.refresh_crisis_assessment(c, "wake")
            request.assert_not_called()
            self.assertIsNone(result)
            self.assertNotIn("crisis_assessment", c.play)
            self.assertEqual(c.world.state, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
