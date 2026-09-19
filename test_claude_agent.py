"""Offline contracts for the resident Claude agent (VIGIL) and the technical brief.

Every transport is mocked: no Claude CLI subprocess, no Anthropic or OpenAI
call, no credential read, no production save. The load-bearing assertions are
structural: an agent request for anything mutating must be impossible, not
merely refused, and a brief that invents numbers must fail validation.
"""
import asyncio
import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

import claude_agent
import engine
import outbreak
import providers
import voyage

OPTIONS = {"destination": "trappist_1_e", "ship": "ship_aurora_ark", "drive": "prop_fusion_continuous", "speed": .03, "launch_year": 2250, "crew": 1024, "scenario": "outbreak"}
PAD = " The mechanism, parameterization and failure modes are examined against the engine's actual finite-resource rules, with the methodological caveats stated explicitly rather than implied."


def result_message(**overrides):
    base = {"subtype": "success", "duration_ms": 1200, "duration_api_ms": 900, "is_error": False,
            "num_turns": 3, "session_id": "test-cli-session", "total_cost_usd": .0123,
            "usage": {"input_tokens": 900, "output_tokens": 400}}
    base.update(overrides)
    return ResultMessage(**base)


def sample_brief(crew_count):
    def section(sid, domain):
        return {
            "id": sid, "domain": domain, "title": f"{domain} assessment",
            "summary": "A grounded reading of the current situation for this domain, at genuine analytical depth for the deciding Admiral.",
            "layers": [
                {"heading": "Overview", "depth": "overview", "body": "What the measured state shows and why it forces a decision now, before the deeper mechanics are unpacked in the layers below." + PAD},
                {"heading": "Technical treatment", "depth": "technical", "body": "The quantitative structure of the problem under the game's explicit mechanics, with derivations kept inspectable." + PAD},
            ],
            "assumptions": ["The engine's finite-resource rules remain the binding constraint set for this window."],
            "uncertainties": ["Individual disease progression is seeded but unscripted, so daily counts carry irreducible variance."],
            "competing_interpretations": ["A containment-first reading and a care-first reading are both defensible on the current evidence."],
            "claims": [{"claim": f"The living crew currently numbers {crew_count}.", "source": "engine:stats.crew"},
                       {"claim": "Secondary attack pressure is a modeled figure, not a measured one.", "source": "modeled_estimate"}],
        }
    return {
        "situation": "Ship year four of the long watch. A seeded outbreak is active, the Admiral is awake, and a 28-day tactical response must be authorized against finite medicine, food and care capacity.",
        "sections": [section("epi", "epidemiology_medicine"), section("gov", "politics_governance"),
                     section("res", "resources_engineering"), section("soc", "social_dynamics")],
    }


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.c = engine.Campaign(engine.make_config(OPTIONS))
        voyage.ensure(self.c)
        self.c.world.tick = 4
        outbreak.start_if_due(self.c)
        voyage.open_incident(self.c, "outbreak")
        adult = next(p for p in self.c.crew() if p["age"] >= 18)
        self.c.play["consultations"].append({
            "id": "deadbeef00000001", "group": "medical", "group_label": "Medical council",
            "question": "Where is the tradeoff?", "revision": self.c.play["revision"], "year": 4,
            "people": 1, "eligible_total": 1, "sampled": False, "sampling_note": "test",
            "mean_distribution": {"contain": .6, "care": .3, "conserve": .1},
            "labels": {"contain": "Contain + care", "care": "Care + keep production", "conserve": "Contain + conserve medicine"},
            "options": [], "synthesis": "Contain.", "experts": [], "voice_label": "test", "distribution_note": "test",
            "seconds": 1.0, "members": [adult],
            "positions": {adult["id"]: {"probabilities": {"contain": .6, "care": .3, "conserve": .1}, "choice": "contain", "confidence": .7}},
        })
        self.adult_id = adult["id"]
        self.c.save = MagicMock()

    def tools_by_name(self):
        session = {"events": [], "brief": None, "answer": ""}
        snapshot = claude_agent.build_snapshot(self.c)
        return {t.name: t for t in claude_agent.build_tools(snapshot, session)}, session, snapshot

    def call(self, tool, args):
        return json.loads(asyncio.run(tool.handler(args))["content"][0]["text"])

    # === Structural authority boundary ===
    def test_no_mutating_tool_exists(self):
        tools, _, _ = self.tools_by_name()
        self.assertEqual(set(tools), set(claude_agent.TOOL_NAMES))
        # Forbidden operations must be absent as name segments; "consultation_evidence"
        # (completed-evidence reads) is legitimate and must never become "consult".
        for forbidden in ("execute", "authorize", "commit", "advance", "order", "cryo", "consult", "poll", "save", "write"):
            self.assertFalse(any(forbidden in name.split("_") for name in tools), forbidden)

    def test_options_disable_every_builtin_capability(self):
        tools, _, _ = self.tools_by_name()
        server = claude_agent.create_sdk_mcp_server("ship", tools=list(tools.values()))
        options = claude_agent._agent_options("claude-sonnet-5", server)
        self.assertEqual(options.tools, [])  # no Claude Code built-ins at all
        self.assertEqual(sorted(options.allowed_tools), sorted(f"mcp__ship__{n}" for n in claude_agent.TOOL_NAMES))
        for name in ("Bash", "Write", "Edit", "WebFetch", "Task"):
            self.assertIn(name, options.disallowed_tools)
        self.assertEqual(options.max_turns, claude_agent.MAX_TURNS)

    def test_every_tool_runs_against_a_deepcopy_and_cannot_touch_the_campaign(self):
        before = claude_agent.world_fingerprint(self.c)
        play_before = copy.deepcopy(self.c.play)
        tools, session, snapshot = self.tools_by_name()
        fuzz = {"ship_state": {}, "mission_profile": {}, "incident_report": {},
                "crew_overview": {"group_by": "health"}, "crew_profile": {"person_id": self.adult_id},
                "consultation_evidence": {"consultation_id": "deadbeef00000001"}, "current_brief": {},
                "submit_brief": {"brief": {"malicious": "../../state", "world": None}}}
        for name, args in fuzz.items():
            self.call(tools[name], args)
        # Even direct hostile mutation of what the tools can reach only hits the copy.
        snapshot["stats"]["crew"] = -1
        snapshot["crew"].clear()
        self.assertEqual(claude_agent.world_fingerprint(self.c), before)
        self.assertEqual(self.c.play, play_before)
        self.c.save.assert_not_called()

    def test_read_tools_return_actual_state(self):
        tools, _, snapshot = self.tools_by_name()
        state = self.call(tools["ship_state"], {})
        self.assertEqual(state["stats"]["crew"], self.c.summary()["crew"])
        report = self.call(tools["incident_report"], {})
        self.assertEqual(report["incident"]["kind"], "outbreak")
        overview = self.call(tools["crew_overview"], {"group_by": "room"})
        self.assertEqual(sum(overview["counts"].values()), len(snapshot["crew"]))
        person = self.call(tools["crew_profile"], {"person_id": self.adult_id})
        self.assertEqual(person["person"]["id"], self.adult_id)
        self.assertIn("deadbeef00000001", person["council_positions"])
        index = self.call(tools["consultation_evidence"], {})
        self.assertEqual(index["consultations"][0]["id"], "deadbeef00000001")
        detail = self.call(tools["consultation_evidence"], {"consultation_id": "deadbeef00000001"})
        self.assertNotIn("positions", detail)
        self.assertEqual(detail["position_choices"][self.adult_id], "contain")

    # === Brief contract ===
    def test_brief_validation_accepts_the_contract_and_stores_a_copy(self):
        tools, session, snapshot = self.tools_by_name()
        brief = sample_brief(snapshot["stats"]["crew"])
        verdict = self.call(tools["submit_brief"], {"brief": brief})
        self.assertTrue(verdict["accepted"], verdict)
        self.assertEqual(session["brief"], brief)
        brief["sections"].clear()  # caller's later mutation must not reach the stored draft
        self.assertEqual(len(session["brief"]["sections"]), 4)

    def test_brief_validation_rejects_missing_domains_and_shallow_layers(self):
        snapshot = claude_agent.build_snapshot(self.c)
        brief = sample_brief(snapshot["stats"]["crew"])
        brief["sections"] = brief["sections"][:3]
        problems = claude_agent.validate_brief(brief, snapshot)
        self.assertTrue(any("Missing required domain" in p or "4-8" in p for p in problems), problems)
        brief = sample_brief(snapshot["stats"]["crew"])
        for layer in brief["sections"][0]["layers"]:
            layer["depth"] = "overview"
        problems = claude_agent.validate_brief(brief, snapshot)
        self.assertTrue(any("technical" in p for p in problems), problems)
        brief = sample_brief(snapshot["stats"]["crew"])
        brief["sections"][0]["competing_interpretations"] = []
        self.assertTrue(claude_agent.validate_brief(brief, snapshot))

    def test_brief_validation_rejects_invented_engine_numbers(self):
        snapshot = claude_agent.build_snapshot(self.c)
        brief = sample_brief(snapshot["stats"]["crew"])
        brief["sections"][0]["claims"][0]["source"] = "engine:stats.invented_metric"
        problems = claude_agent.validate_brief(brief, snapshot)
        self.assertTrue(any("does not resolve" in p for p in problems), problems)
        brief["sections"][0]["claims"][0]["source"] = "just_trust_me"
        problems = claude_agent.validate_brief(brief, snapshot)
        self.assertTrue(any("source" in p for p in problems), problems)

    # === Mocked-transport agent loop ===
    def run_job(self, data, query_fn):
        frames = []
        result = claude_agent.run_agent_job(self.c, data, frames.append, query_fn=query_fn)
        return result, frames

    def make_query(self, submit=True, mutate=None):
        campaign = self.c
        captured = {}
        original = claude_agent.build_tools

        def spy(snapshot, session):
            tools = original(snapshot, session)
            captured["tools"] = {t.name: t for t in tools}
            captured["snapshot"] = snapshot
            return tools

        async def fake_query(*, prompt, options, **_):
            yield AssistantMessage(content=[TextBlock(text="Surveying the saved ship state.")], model="claude-sonnet-5")
            state = json.loads((await captured["tools"]["ship_state"].handler({}))["content"][0]["text"])
            if submit:
                verdict = json.loads((await captured["tools"]["submit_brief"].handler({"brief": sample_brief(state["stats"]["crew"])}))["content"][0]["text"])
                assert verdict["accepted"], verdict
            if mutate:
                mutate(campaign)
            yield AssistantMessage(content=[TextBlock(text="Brief compiled; the binding constraint is medicine against care demand.")], model="claude-sonnet-5")
            yield result_message()
        return fake_query, spy

    def test_brief_run_streams_events_and_persists_through_the_normal_save_path(self):
        fingerprint = claude_agent.world_fingerprint(self.c)
        fake_query, spy = self.make_query()
        with patch.object(claude_agent, "build_tools", side_effect=spy):
            result, frames = self.run_job({"mode": "brief"}, fake_query)
        self.assertEqual(claude_agent.world_fingerprint(self.c), fingerprint)
        self.c.save.assert_called_once()
        play = self.c.play
        self.assertEqual(play["technical_brief"]["provenance"]["provider"], "claude")
        self.assertEqual(play["technical_brief"]["provenance"]["revision"], play["revision"])
        self.assertEqual(len(play["agent_activity"]), 1)
        record = play["agent_activity"][0]
        self.assertTrue(record["read_only"])
        kinds = [e["kind"] for e in record["events"]]
        self.assertIn("tool_call", kinds)
        self.assertIn("tool_result", kinds)
        self.assertIn("result", kinds)
        self.assertTrue(frames and frames[-1]["agent"]["status"] == "complete")
        self.assertEqual(result["agent"]["attribution"], "CLAUDE · ship intelligence")
        self.assertEqual(result["agent"]["model"], "claude-sonnet-5")
        self.assertTrue(result["read_only"])
        public = self.c.public()
        self.assertIn("technical_brief", public["play"])
        self.assertNotIn("advisory_audits", public["play"])
        self.assertEqual(play["advisory_audits"][-1]["purpose"], "resident_agent_run")
        self.assertEqual(play["agent_session"]["session_id"], "test-cli-session")

    def test_ask_mode_answers_without_a_brief(self):
        fake_query, spy = self.make_query(submit=False)
        with patch.object(claude_agent, "build_tools", side_effect=spy):
            result, _ = self.run_job({"mode": "ask", "question": "What is the binding constraint?"}, fake_query)
        self.assertIn("binding constraint", result["agent"]["answer"])
        self.assertNotIn("technical_brief", self.c.play)
        self.assertEqual(self.c.play["agent_activity"][0]["mode"], "ask")

    def test_brief_run_without_valid_submission_fails_closed(self):
        fake_query, spy = self.make_query(submit=False)
        with patch.object(claude_agent, "build_tools", side_effect=spy):
            with self.assertRaises(RuntimeError):
                self.run_job({"mode": "brief"}, fake_query)
        self.c.save.assert_not_called()
        self.assertNotIn("technical_brief", self.c.play)

    def test_fingerprint_guard_rejects_a_transport_that_mutates_the_world(self):
        def sabotage(campaign):
            campaign.world.state["resources"]["food_kg"] += 1e6
        fake_query, spy = self.make_query(submit=True, mutate=sabotage)
        with patch.object(claude_agent, "build_tools", side_effect=spy):
            with self.assertRaises(RuntimeError):
                self.run_job({"mode": "brief"}, fake_query)
        self.c.save.assert_not_called()
        self.assertNotIn("technical_brief", self.c.play)

    def test_input_bounds(self):
        with self.assertRaises(ValueError):
            claude_agent.run_agent_job(self.c, {"mode": "invade"}, lambda *_: None, query_fn=object())
        with self.assertRaises(ValueError):
            claude_agent.run_agent_job(self.c, {"mode": "ask", "question": "hi"[:2]}, lambda *_: None, query_fn=object())

    # === Provider routing ===
    def test_astra_provider_routes_the_brief_without_touching_claude(self):
        snapshot = claude_agent.build_snapshot(self.c)
        reply = {"model": "test-astra-returned", "id": "resp_1", "usage": {"total_tokens": 10},
                 "output": [{"type": "function_call", "name": "submit_brief",
                             "arguments": json.dumps({"brief": sample_brief(snapshot["stats"]["crew"])})}]}

        def never_claude(**_):
            raise AssertionError("The Claude transport must not run on the astra path.")
        with patch.dict(os.environ, {"PROXIMA_BRIEF_PROVIDER": "astra"}), \
                patch.object(claude_agent, "credential", return_value="test-not-a-secret"), \
                patch.object(claude_agent, "request_json", return_value=reply) as sent:
            result, _ = self.run_job({"mode": "brief"}, never_claude)
        self.assertEqual(result["agent"]["provider"], "astra")
        self.assertEqual(self.c.play["technical_brief"]["provenance"]["provider"], "astra")
        self.assertEqual(sent.call_args.args[0], "https://api.openai.com/v1/responses")
        payload = sent.call_args.args[2]
        self.assertEqual(payload["tool_choice"], {"type": "function", "name": "submit_brief"})
        self.assertNotIn("crew", json.loads(payload["input"])["snapshot"])  # size discipline

    def test_astra_provider_rejects_a_contract_violating_brief(self):
        bad = {"model": "m", "output": [{"type": "function_call", "name": "submit_brief",
                                         "arguments": json.dumps({"brief": {"situation": "too short"}})}]}
        with patch.dict(os.environ, {"PROXIMA_BRIEF_PROVIDER": "astra"}), \
                patch.object(claude_agent, "credential", return_value="x"), \
                patch.object(claude_agent, "request_json", return_value=bad):
            with self.assertRaises(RuntimeError):
                self.run_job({"mode": "brief"}, None)
        self.assertNotIn("technical_brief", self.c.play)

    def test_brief_provider_switch(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROXIMA_BRIEF_PROVIDER", None)
            self.assertEqual(providers.brief_provider(), "claude")
        with patch.dict(os.environ, {"PROXIMA_BRIEF_PROVIDER": "astra"}):
            self.assertEqual(providers.brief_provider(), "astra")
        with patch.dict(os.environ, {"PROXIMA_BRIEF_PROVIDER": "gemini"}):
            with self.assertRaises(RuntimeError):
                providers.brief_provider()

    def test_ask_resumes_only_a_matching_model_session(self):
        self.c.play["agent_session"] = {"session_id": "old-session", "model": "claude-sonnet-5"}
        seen = {}

        async def fake_query(*, prompt, options, **_):
            seen["resume"] = options.resume
            yield AssistantMessage(content=[TextBlock(text="ok")], model="claude-sonnet-5")
            yield result_message(session_id="new-session")
        claude_agent.run_agent_job(self.c, {"mode": "ask", "question": "Status?"}, lambda *_: None, query_fn=fake_query)
        self.assertEqual(seen["resume"], "old-session")
        self.assertEqual(self.c.play["agent_session"]["session_id"], "new-session")

    # === Server registration stays minimal but present ===
    def test_server_exposes_the_agent_route(self):
        source = (Path(__file__).parent / "server.py").read_text()
        self.assertIn('"/api/agent": "agent"', source)
        self.assertIn('kind == "agent"', source)
        self.assertIn("run_agent_job", source)

    def test_readiness_reports_claude_lane(self):
        report = providers.readiness()
        self.assertIn("claude", report)
        self.assertEqual(report["claude"]["model"], providers.CLAUDE_MODEL)


if __name__ == "__main__":
    unittest.main()
