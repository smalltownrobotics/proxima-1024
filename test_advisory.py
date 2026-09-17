"""Offline council contracts: real roster membership, complete model data, no orders.

Every provider request is mocked. Test campaigns remain in memory; these tests do
not read credentials, call a model, or change production saves.
"""
import copy
import json
import unittest
from unittest.mock import patch

import advisory
import engine
import live_voyage
import outbreak
import voyage


# === Synthetic provider fixtures and strong non-mutation snapshot ===
OPTIONS = {"destination": "trappist_1_e", "ship": "ship_aurora_ark", "drive": "prop_fusion_continuous", "speed": .03, "launch_year": 2250, "crew": 1024, "scenario": "outbreak"}


def snapshot(c):
    return copy.deepcopy({"world": c.world.state, "rng": engine.rng_dump(c.world.rng), "tick": c.world.tick, "earth_year": c.world.earth_year, "sim_year": c.world.sim_year, "config": c.config, "play": c.play, "reactions": c.reactions, "history": c.history, "jev_runs": c.jev_runs, "astra_runs": c.astra_runs, "messages": c.messages, "focus": c.focus, "status": c.status})


def provider(url, _key, payload):
    if "typesafe" in url:
        answers = {}
        for name, question in payload["questions"].items():
            labels = list(question["criteria"])
            answers[name] = {"probabilities": dict(zip(labels, (.55, .30, .15))), "confidence": .72}
        return {"model": "test-jev-actual-returned-name", "answers": answers}
    state = json.loads(payload["input"])
    content = {"synthesis": "The council sees a real tradeoff between contacts, care and the food buffer.", "experts": [{"person_id": person["id"], "statement": "I favor containment with care, while accounting for the lost harvest."} for person in state["speakers"]]}
    return {"model": "test-astra-returned-name", "output": [{"type": "function_call", "name": "report_council", "arguments": json.dumps(content)}]}


class AdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.c = engine.Campaign(engine.make_config(OPTIONS))
        voyage.ensure(self.c)
        self.c.world.tick = 4
        outbreak.start_if_due(self.c)
        voyage.open_incident(self.c, "outbreak")

    def run_consult(self, group="medical", person_id=None, request=provider):
        frames = []
        with patch.object(advisory, "credential", return_value="test-not-a-secret"), patch.object(advisory, "request_json", side_effect=request), patch.object(self.c, "save") as save:
            result = advisory.consult(self.c, group, "Where is the best tradeoff for this ship?", frames.append, person_id)
            save.assert_not_called()
        return result, frames

    # === Existing people only, stable fictional dressing, truthful sample size ===
    def test_profiles_stable_and_experience_bounded_by_actual_age(self):
        first = self.c.crew()
        second = self.c.crew()
        self.assertEqual(first, second)
        for person in first:
            self.assertTrue(person["synthetic_profile"])
            self.assertTrue(person["name"])
            self.assertTrue(person["specialty"])
            self.assertLessEqual(person["experience_years"], max(0, person["age"] - 18))
        self.assertEqual(len(first), self.c.summary()["crew"])

    def test_group_bounds_and_deterministic_sampling(self):
        actual = self.c.crew()
        for group, definition in advisory.GROUPS.items():
            selected, total, sampled = advisory.members(self.c, group)
            eligible = [p for p in actual if p["age"] >= 18 and (not definition["rooms"] or p["room"] in definition["rooms"])]
            self.assertEqual(total, len(eligible))
            self.assertEqual(len(selected), min(500, total))
            self.assertEqual(sampled, total > 500)
            self.assertEqual(selected, advisory.members(self.c, group)[0])
            self.assertTrue({p["id"] for p in selected}.issubset({p["id"] for p in eligible}))
        medical, total, sampled = advisory.members(self.c, "medical")
        self.assertLess(total, 500)
        self.assertEqual(len(medical), total)
        self.assertFalse(sampled)

    def test_invalid_groups_children_dead_or_wrong_room_rejected(self):
        people = self.c.world.state["population"]["people"]
        pid = next(iter(people))
        with self.assertRaises(ValueError):
            advisory.members(self.c, "500_doctors")
        people[pid]["age"] = 12
        with self.assertRaises(ValueError):
            advisory.members(self.c, "all", str(pid))
        people[pid]["age"] = 35
        people[pid]["alive"] = False
        with self.assertRaises(ValueError):
            advisory.members(self.c, "all", str(pid))
        engineer = advisory.members(self.c, "engineering")[0][0]
        with self.assertRaises(ValueError):
            advisory.members(self.c, "medical", engineer["id"])

    # === Actual probability data, bounded streaming, no action side effects ===
    def test_consult_streams_fifty_person_batches_and_preserves_world(self):
        before = snapshot(self.c)
        calls = []
        def capture(url, key, payload):
            calls.append((url, copy.deepcopy(payload)))
            return provider(url, key, payload)
        result, frames = self.run_consult("all", request=capture)
        self.assertEqual(snapshot(self.c), before)
        council = result["consultation"]
        jev = [body for url, body in calls if "typesafe" in url]
        astra = [body for url, body in calls if "openai" in url]
        self.assertEqual(len(jev), 10)
        self.assertTrue(all(len(body["questions"]) == 50 for body in jev))
        self.assertEqual(council["received"], 500)
        self.assertEqual(council["people"], 500)
        self.assertGreater(council["eligible_total"], 500)
        self.assertTrue(council["sampled"])
        self.assertTrue(council["read_only"])
        self.assertEqual(frames[0]["council"]["received"], 0)
        self.assertEqual(sorted(set(frame["council"]["received"] for frame in frames)), list(range(0, 501, 50)))
        self.assertAlmostEqual(council["mean_distribution"]["contain"], .55)
        # All argmax labels agree, but the collective result is still .55, not 1.
        self.assertTrue(all(p["choice"] == "contain" for p in council["positions"].values()))
        self.assertEqual(len(council["experts"]), 3)
        self.assertEqual(len(astra), 1)
        self.assertFalse(astra[0]["store"])
        self.assertEqual([t["name"] for t in astra[0]["tools"]], ["report_council"])
        self.assertEqual(len(result["audit"]["jev"]), 10)
        self.assertTrue(all(a["returned_model"] == "test-jev-actual-returned-name" for a in result["audit"]["jev"]))

    def test_single_profile_followup_is_one_actual_person(self):
        person = advisory.members(self.c, "medical")[0][0]
        before = snapshot(self.c)
        result, _ = self.run_consult("medical", person["id"])
        council = result["consultation"]
        self.assertEqual(council["total"], 1)
        self.assertEqual(council["members"], [person])
        self.assertEqual([voice["person_id"] for voice in council["experts"]], [person["id"]])
        self.assertEqual(snapshot(self.c), before)

    def test_options_are_exact_supported_policies_and_barriers_are_visible(self):
        options = advisory.alternatives(self.c)
        self.assertEqual({option["id"]: option["operations"] for option in options}, {"contain": ["isolate", "surge_care"], "care": ["surge_care", "protect_food"], "conserve": ["isolate"]})
        self.c.world.state["resources"]["medicine_kg"] = 0
        before = snapshot(self.c)
        options = {option["id"]: option for option in advisory.alternatives(self.c)}
        self.assertFalse(options["contain"]["available"])
        self.assertFalse(options["care"]["available"])
        self.assertTrue(options["conserve"]["available"])
        self.assertTrue(options["contain"]["barrier"])
        self.assertEqual(snapshot(self.c), before)

    def test_bad_distributions_never_advance_the_world(self):
        bad_values = ({"support": 1, "question": 0, "oppose": 0}, {"contain": float("nan"), "care": 0, "conserve": 0}, {"contain": 0, "care": 0, "conserve": 0}, {"contain": True, "care": 0, "conserve": 0})
        person = advisory.members(self.c, "medical")[0][0]
        for bad in bad_values:
            def invalid(url, key, payload):
                reply = provider(url, key, payload)
                for answer in reply.get("answers", {}).values():
                    answer["probabilities"] = bad
                return reply
            before = snapshot(self.c)
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                self.run_consult("medical", person["id"], invalid)
            self.assertEqual(snapshot(self.c), before)

    def test_astra_cannot_attribute_voice_to_unselected_person(self):
        def invalid(url, key, payload):
            reply = provider(url, key, payload)
            if "openai" in url:
                content = json.loads(reply["output"][0]["arguments"])
                content["experts"][0]["person_id"] = "invented-doctor"
                reply["output"][0]["arguments"] = json.dumps(content)
            return reply
        before = snapshot(self.c)
        result, frames = self.run_consult(request=invalid)
        council = result["consultation"]
        self.assertEqual(council["stage"], "complete")
        self.assertEqual(council["received"], council["total"])
        self.assertEqual(council["experts"], [])
        self.assertEqual(council["synthesis"], "")
        self.assertEqual(council["synthesis_error"], advisory.SYNTHESIS_UNAVAILABLE)
        self.assertEqual(result["audit"]["astra"]["status"], "failed")
        self.assertEqual(result["audit"]["astra"]["stage"], "validation")
        self.assertIn("invented-doctor", json.dumps(result["audit"]["astra"]["response"]))
        self.assertNotIn("invented-doctor", json.dumps(council))
        self.assertEqual(frames[-1]["council"], council)
        self.assertEqual(snapshot(self.c), before)

    def test_astra_network_failure_preserves_completed_jev_without_retry(self):
        calls = []
        def failed_voice(url, key, payload):
            calls.append(url)
            if "openai" in url:
                raise RuntimeError("PRIVATE_PROVIDER_FAILURE_SENTINEL")
            return provider(url, key, payload)
        before = snapshot(self.c)
        result, frames = self.run_consult("all", request=failed_voice)
        council = result["consultation"]
        self.assertEqual(snapshot(self.c), before)
        self.assertEqual(sum("typesafe" in url for url in calls), 10)
        self.assertEqual(sum("openai" in url for url in calls), 1)
        self.assertEqual(council["received"], 500)
        self.assertEqual(len(council["positions"]), 500)
        self.assertEqual(council["stage"], "complete")
        self.assertTrue(council["read_only"])
        self.assertEqual(council["synthesis"], "")
        self.assertEqual(council["experts"], [])
        for key, expected in (("contain", .55), ("care", .30), ("conserve", .15)):
            self.assertAlmostEqual(council["mean_distribution"][key], expected)
        self.assertEqual(frames[-1]["council"], council)
        self.assertIn("unavailable", frames[-1]["phase"])
        self.assertEqual(len(result["audit"]["jev"]), 10)
        astra = result["audit"]["astra"]
        self.assertEqual(astra["status"], "failed")
        self.assertEqual(astra["stage"], "request")
        self.assertEqual(astra["error_type"], "RuntimeError")
        self.assertIn("request", astra)
        self.assertNotIn("response", astra)
        self.assertNotIn("PRIVATE_PROVIDER_FAILURE_SENTINEL", json.dumps(result))

    def test_malformed_voice_keeps_private_reply_but_not_public_testimony(self):
        def malformed(url, key, payload):
            if "openai" in url:
                return {"model": "test-astra", "output": [{"type": "function_call", "name": "report_council", "arguments": "PRIVATE_INVALID_JSON_SENTINEL"}]}
            return provider(url, key, payload)
        before = snapshot(self.c)
        result, _ = self.run_consult(request=malformed)
        council = result["consultation"]
        self.assertEqual(snapshot(self.c), before)
        self.assertEqual(council["received"], council["total"])
        self.assertEqual(council["stage"], "complete")
        self.assertEqual(council["experts"], [])
        self.assertIn("synthesis_error", council)
        self.assertIn("PRIVATE_INVALID_JSON_SENTINEL", json.dumps(result["audit"]["astra"]["response"]))
        self.c.play["consultations"] = [council]
        self.c.play["advisory_audits"] = [result["audit"]]
        public = self.c.public()
        self.assertNotIn("PRIVATE_INVALID_JSON_SENTINEL", json.dumps(public))
        self.assertEqual(public["play"]["consultations"][0]["synthesis_error"], advisory.SYNTHESIS_UNAVAILABLE)

    def test_assessment_is_one_read_only_jev_request(self):
        before = snapshot(self.c)
        with patch.object(advisory, "credential", return_value="test-not-a-secret"), patch.object(advisory, "request_json", side_effect=provider) as request:
            result = advisory.assess(self.c)
        self.assertEqual(request.call_count, 1)
        self.assertIn("typesafe", request.call_args.args[0])
        self.assertEqual(snapshot(self.c), before)
        summary = result["summary"]
        for key, labels in advisory.ASSESSMENT_LABELS.items():
            self.assertEqual(set(summary[key]["probabilities"]), set(labels))
            self.assertAlmostEqual(sum(summary[key]["probabilities"].values()), 1)
        self.assertEqual(summary["priority"], summary["urgency"]["choice"])

    def test_public_campaign_omits_private_advisory_audit(self):
        self.c.play["advisory_audits"] = [{"request": "PRIVATE_COUNCIL_SENTINEL", "response": "PRIVATE_ASSESSMENT_SENTINEL"}]
        self.c.play["crisis_assessment"] = {"priority": "immediate", "read_only": True}
        public = self.c.public()
        self.assertNotIn("advisory_audits", public["play"])
        self.assertNotIn("PRIVATE_COUNCIL_SENTINEL", json.dumps(public))
        self.assertEqual(public["play"]["crisis_assessment"]["priority"], "immediate")

    def test_astra_context_keeps_sample_and_speaker_provenance_without_full_roster(self):
        report, _ = self.run_consult("all")
        council = report["consultation"]
        self.c.play["consultations"] = [council]
        before = snapshot(self.c)
        compact = live_voyage.context(self.c)["councils"][0]
        self.assertEqual(snapshot(self.c), before)
        for key in ("people", "eligible_total", "sampled", "sampling_note", "voice_label", "revision", "mean_distribution", "labels"):
            self.assertEqual(compact[key], council[key])
        self.assertEqual(compact["people"], 500)
        self.assertGreater(compact["eligible_total"], 500)
        self.assertTrue(compact["sampled"])
        self.assertNotIn("members", compact)
        self.assertNotIn("positions", compact)
        self.assertEqual(len(compact["experts"]), 3)
        members = {person["id"]: person for person in council["members"]}
        for expert in compact["experts"]:
            member = members[expert["person_id"]]
            self.assertEqual(expert["name"], member["name"])
            self.assertEqual(expert["specialty"], member["specialty"])
            self.assertEqual(expert["position"], council["positions"][expert["person_id"]])
            self.assertTrue(expert["synthetic_profile"])
        self.assertLess(len(json.dumps(compact)), 8000)
        # Context is a detached summary, not a mutable alias into saved advice.
        compact["experts"][0]["position"]["probabilities"]["contain"] = 0
        self.assertEqual(snapshot(self.c), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
