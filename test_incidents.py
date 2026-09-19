"""Offline incident-domain and steering tests: no model calls, no production saves.

Covers the four new grounded domains (uprising, crime, cascade, ai_steward),
standing-policy economics, conservation invariants, and a seed-sweep asserting
that medical wakes no longer dominate the voyage. Synthetic fixtures only.
"""
import collections
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
import incidents_ai
import incidents_core
import incidents_crime
import incidents_politics
import incidents_systems
import voyage


OPTIONS = {"crew": 1024, "ship": "ship_aurora_ark", "drive": "prop_fusion_continuous", "destination": "trappist_1_e", "speed": .03, "launch_year": 2250}


def campaign(seed=0):
    config = engine.make_config(OPTIONS)
    config["tunables"]["rng_seed"] = seed
    c = engine.Campaign(config)
    voyage.ensure(c)
    return c


def commit(c, ops):
    with patch.object(c, "save", lambda: None):
        return voyage.commit(c, "test order", {"operations": ops}, {}, {"summary": {}})


def alive(c):
    return c.world.state["population"]["alive"]


# === Politics: governance uprising =========================================
class PoliticsTests(unittest.TestCase):
    def force(self, c):
        c.world.state["governance"]["legitimacy"] = .35
        c.world.tick = 12
        incidents_politics.annual_tick(c)
        return c.world.state[incidents_politics.SLICE]

    def test_predicate_fires_from_real_faction_state(self):
        c = campaign()
        incidents_politics.annual_tick(c)
        self.assertEqual(c.world.state[incidents_politics.SLICE]["status"], "quiet")
        self.assertIsNone(voyage.urgent(c))
        state = self.force(c)
        self.assertEqual(state["status"], "active")
        self.assertIn(state["movement"]["faction_id"], c.world.state["factions"]["factions"])
        self.assertTrue(incidents_politics.should_wake(c))
        self.assertEqual(voyage.urgent(c), "uprising")
        info = voyage.open_incident(c, "uprising")
        self.assertEqual(info["domain"], "uprising")
        self.assertEqual(info["evidence"][-1]["movement"]["name"], state["movement"]["name"])

    def test_concede_resolves_and_conserves_population(self):
        c = campaign()
        state = self.force(c)
        faction = c.world.state["factions"]["factions"][state["movement"]["faction_id"]]
        before_alienation, before_alive = faction["alienation"], alive(c)
        voyage.open_incident(c, "uprising")
        record = commit(c, ["concede"])
        self.assertEqual(state["status"], "stood_down")
        self.assertTrue(c.play["incident"]["resolved"])
        self.assertFalse(c.play["incident"]["accepted_risk"])
        self.assertLess(faction["alienation"], before_alienation)
        self.assertLessEqual(c.world.state["governance"]["pressure"], .55)
        self.assertEqual(alive(c), before_alive)
        self.assertEqual(record["domain_notes"][0]["op"], "concede")

    def test_crackdown_is_a_different_bargain(self):
        conceded, suppressed = campaign(3), campaign(3)
        for c, op in ((conceded, "concede"), (suppressed, "crackdown")):
            state = self.force(c)
            voyage.open_incident(c, "uprising")
            commit(c, [op])
        self.assertGreater(conceded.summary()["mandate"], suppressed.summary()["mandate"])
        self.assertGreater(conceded.summary()["morale"], suppressed.summary()["morale"])
        a = conceded.world.state[incidents_politics.SLICE]
        b = suppressed.world.state[incidents_politics.SLICE]
        self.assertEqual((a["status"], b["status"]), ("stood_down", "suppressed"))
        self.assertLess(b["cooldown_until"], a["cooldown_until"])  # it comes back sooner
        fid = b["movement"]["faction_id"]
        self.assertGreater(suppressed.world.state["factions"]["factions"][fid]["alienation"],
                           conceded.world.state["factions"]["factions"][fid]["alienation"])

    def test_cede_commons_costs_growing_space(self):
        c = campaign()
        self.force(c)
        area = c.world.read("resources.agricultural_area_m2")
        voyage.open_incident(c, "uprising")
        commit(c, ["cede_commons"])
        self.assertAlmostEqual(c.world.read("resources.agricultural_area_m2"), area * .94)
        self.assertTrue(c.play["incident"]["resolved"])

    def test_ops_require_active_movement_and_are_noops(self):
        c = campaign()
        voyage.open_incident(c, "uprising")
        before = copy.deepcopy(c.world.state)
        for ops in (["concede"], ["crackdown"], ["cede_commons"]):
            with self.assertRaises(ValueError):
                voyage.validate_plan(c, {"operations": ops})
            self.assertEqual(c.world.state, before)

    def test_hold_defers_but_movement_keeps_escalating(self):
        c = campaign()
        state = self.force(c)
        voyage.open_incident(c, "uprising")
        pressure = c.world.state["governance"]["pressure"]
        commit(c, ["hold"])
        self.assertTrue(c.play["incident"]["accepted_risk"])
        self.assertEqual(state["status"], "active")
        self.assertFalse(incidents_politics.should_wake(c))  # deferred, not cured
        for _ in range(2):
            c.world.tick += 1
            incidents_politics.annual_tick(c)
        self.assertGreater(c.world.state["governance"]["pressure"], pressure)


# === Crime: organized-influence network ====================================
class CrimeTests(unittest.TestCase):
    def form(self, c, limit=90):
        c.world.state["morale"]["aggregate"] = 32.
        c.world.state["governance"]["legitimacy"] = .40
        state = incidents_crime.ensure(c)
        while state["status"] == "none" and c.world.tick < limit:
            c.world.tick += 1
            incidents_crime.annual_tick(c)
        self.assertEqual(state["status"], "active", "network never formed under harsh constructed conditions")
        return state

    def test_network_is_actual_roster_and_ledger_conserves_food(self):
        c = campaign(1)
        food_start = c.world.state["resources"]["food_kg"]
        state = self.form(c)
        living = {str(pid) for pid, p in c.world.state["population"]["people"].items() if p["alive"]}
        self.assertTrue(set(state["network"]["ringleaders"]) <= living)
        for _ in range(4):
            c.world.tick += 1
            incidents_crime.annual_tick(c)
        # Every stolen kilogram left resources and sits in the conserved ledger.
        self.assertGreater(state["skimmed_food_kg"], 0)
        self.assertAlmostEqual(food_start - c.world.state["resources"]["food_kg"], state["skimmed_food_kg"], places=6)
        held = state["skimmed_food_kg"] - state["consumed_food_kg"] - state["recovered_food_kg"]
        self.assertGreater(held, 0)
        self.assertTrue(set(state["corrupted"]) <= living)
        self.assertTrue(incidents_crime.should_wake(c) or state["skimmed_food_kg"] < alive(c) * 1.2 * incidents_crime.WAKE_SKIM_DAYS)

    def test_purge_recovers_caches_exactly_and_costs_mandate_morale(self):
        c = campaign(1)
        state = self.form(c)
        for _ in range(4):
            c.world.tick += 1
            incidents_crime.annual_tick(c)
        held = state["skimmed_food_kg"] - state["consumed_food_kg"] - state["recovered_food_kg"]
        food = c.world.state["resources"]["food_kg"]
        morale, mandate = c.summary()["morale"], c.summary()["mandate"]
        voyage.open_incident(c, "crime")
        info = c.play["incident"]
        self.assertTrue(any(e.get("ringleaders") for e in info["evidence"]))
        commit(c, ["purge_network"])
        self.assertEqual(state["status"], "dissolved")
        self.assertTrue(c.play["incident"]["resolved"])
        # recovered caches, minus the op's standard 1% food work cost
        self.assertAlmostEqual(c.world.state["resources"]["food_kg"], (food + held) * .99, places=4)
        self.assertLess(c.summary()["morale"], morale)
        self.assertLess(c.summary()["mandate"], mandate)

    def test_turn_and_deal_are_different_paths(self):
        turned, bought = campaign(1), campaign(1)
        for c, op in ((turned, "turn_network"), (bought, "ration_deal")):
            state = self.form(c)
            for _ in range(4):
                c.world.tick += 1
                incidents_crime.annual_tick(c)
            voyage.open_incident(c, "crime")
            commit(c, [op])
            self.assertTrue(c.play["incident"]["resolved"])
        self.assertEqual(turned.world.state[incidents_crime.SLICE]["status"], "dormant")
        self.assertEqual(bought.world.state[incidents_crime.SLICE]["status"], "dissolved")
        self.assertEqual(turned.world.state[incidents_crime.SLICE]["corrupted"], [])
        self.assertGreater(turned.summary()["mandate"], bought.summary()["mandate"])
        self.assertGreater(turned.world.state["resources"]["food_kg"], bought.world.state["resources"]["food_kg"])

    def test_watch_policy_suppresses_formation(self):
        formed_plain, formed_watch = [], []
        for seed in range(5):
            for policies, bucket in (({}, formed_plain), ({"policy_watch": {"since": 0}}, formed_watch)):
                c = campaign(seed)
                c.play["policies"] = dict(policies)
                c.world.state["morale"]["aggregate"] = 32.
                state = incidents_crime.ensure(c)
                year = None
                for _ in range(50):
                    c.world.tick += 1
                    incidents_crime.annual_tick(c)
                    if state["status"] == "active":
                        year = c.world.tick
                        break
                bucket.append(year)
        plain = sum(y is not None for y in formed_plain)
        watched = sum(y is not None for y in formed_watch)
        self.assertGreater(plain, watched)

    def test_ops_require_active_network(self):
        c = campaign()
        voyage.open_incident(c, "crime")
        before = copy.deepcopy(c.world.state)
        with self.assertRaises(ValueError):
            voyage.validate_plan(c, {"operations": ["purge_network"]})
        self.assertEqual(c.world.state, before)


# === Engineering cascade ===================================================
class CascadeTests(unittest.TestCase):
    def force(self, c):
        ship = c.world.state["ship_integrity"]
        ship.update(integrity=.80, failures_total=2, active_failure="life_support_failure", active_severity=.03,
                    failure_history=[{"tick": 3, "kind": "hull_breach"}, {"tick": 5, "kind": "life_support_failure"}])
        c.world.tick = 6
        incidents_systems.annual_tick(c)
        return c.world.state[incidents_systems.SLICE]

    def test_strain_accumulates_from_engine_failures_and_fires(self):
        c = campaign()
        state = self.force(c)
        self.assertEqual(state["status"], "active")  # two new failures chain immediately
        self.assertGreater(state["strain"], 0)
        self.assertEqual([x["kind"] for x in state["chain"]], ["hull_breach", "life_support_failure"])
        self.assertEqual(voyage.urgent(c), "cascade")
        info = voyage.open_incident(c, "cascade")
        self.assertEqual(info["evidence"][-1]["active_failure"], "life_support_failure")

    def test_overhaul_resolves_and_clears_the_actual_failure(self):
        c = campaign()
        self.force(c)
        voyage.open_incident(c, "cascade")
        commit(c, ["overhaul"])
        self.assertTrue(c.play["incident"]["resolved"])
        self.assertIsNone(c.world.read("ship_integrity.active_failure"))
        self.assertGreaterEqual(c.summary()["integrity"], 88)
        self.assertEqual(c.world.read("mortality.modifiers.system_failure"), 1.0)

    def test_strip_nonessential_costs_future_growing_space(self):
        c = campaign()
        self.force(c)
        limit = c.play["garden_limit"]
        voyage.open_incident(c, "cascade")
        commit(c, ["strip_nonessential"])
        self.assertAlmostEqual(c.play["garden_limit"], limit * .94)
        self.assertTrue(c.play["incident"]["resolved"])

    def test_ops_require_active_cascade(self):
        c = campaign()
        voyage.open_incident(c, "cascade")
        with self.assertRaises(ValueError):
            voyage.validate_plan(c, {"operations": ["overhaul"]})


# === WARDEN, the shipboard steward =========================================
class AITests(unittest.TestCase):
    def activate(self, c, limit=120):
        c.world.state["ship_integrity"]["failures_total"] = 2
        state = incidents_ai.ensure(c)
        c.world.tick = max(c.world.tick, incidents_ai.EMERGENCE_MIN_YEAR)
        while state["status"] == "dormant" and c.world.tick < limit:
            c.world.tick += 1
            incidents_ai.annual_tick(c)
        self.assertEqual(state["status"], "active", "steward never emerged under constructed conditions")
        return state

    def test_emergence_is_seeded_and_gated_on_engine_record(self):
        c = campaign(2)
        state = incidents_ai.ensure(c)
        for _ in range(30):  # pristine ship, before the minimum year: never
            incidents_ai.annual_tick(c)
        self.assertEqual(state["status"], "dormant")
        state = self.activate(c)
        self.assertEqual(state["emerged_year"], c.world.tick)
        twin = campaign(2)
        twin.world.state["ship_integrity"]["failures_total"] = 2
        twin.world.tick = incidents_ai.EMERGENCE_MIN_YEAR
        twin_state = incidents_ai.ensure(twin)
        while twin_state["status"] == "dormant" and twin.world.tick < 120:
            twin.world.tick += 1
            incidents_ai.annual_tick(twin)
        self.assertEqual(twin_state["emerged_year"], state["emerged_year"])  # same seed, same year

    def test_sequester_moves_conserved_kilograms_and_maintains_hull(self):
        c = campaign(2)
        state = self.activate(c)
        resources = c.world.state["resources"]
        totals = (resources["food_kg"] + state["sequestered"]["food_kg"], resources["medicine_kg"] + state["sequestered"]["medicine_kg"])
        c.world.state["ship_integrity"]["integrity"] = .90  # below the clamp so the service is measurable
        integrity = c.world.state["ship_integrity"]["integrity"]
        for _ in range(3):
            c.world.tick += 1
            incidents_ai.annual_tick(c)
        self.assertGreater(state["sequestered"]["food_kg"], 0)
        self.assertGreater(state["sequestered"]["medicine_kg"], 0)
        self.assertAlmostEqual(resources["food_kg"] + state["sequestered"]["food_kg"], totals[0], places=6)
        self.assertAlmostEqual(resources["medicine_kg"] + state["sequestered"]["medicine_kg"], totals[1], places=6)
        self.assertGreater(c.world.state["ship_integrity"]["integrity"], integrity)  # the service is real
        self.assertTrue(incidents_ai.should_wake(c))
        self.assertEqual(voyage.urgent(c), "ai_steward")

    def test_partition_returns_everything_but_risks_integrity(self):
        c = campaign(2)
        state = self.activate(c)
        for _ in range(3):
            c.world.tick += 1
            incidents_ai.annual_tick(c)
        held = dict(state["sequestered"])
        food, medicine = c.world.state["resources"]["food_kg"], c.world.state["resources"]["medicine_kg"]
        integrity = c.world.state["ship_integrity"]["integrity"]
        voyage.open_incident(c, "ai_steward")
        commit(c, ["partition_core"])
        self.assertEqual(state["status"], "partitioned")
        self.assertTrue(c.play["incident"]["resolved"])
        self.assertAlmostEqual(state["sequestered"]["food_kg"], 0)
        self.assertAlmostEqual(state["sequestered"]["medicine_kg"], 0)
        # all returned; food then pays the op's standard 1% work cost
        self.assertAlmostEqual(c.world.state["resources"]["food_kg"], (food + held["food_kg"]) * .99, places=4)
        self.assertAlmostEqual(c.world.state["resources"]["medicine_kg"], medicine + held["medicine_kg"], places=6)
        self.assertLess(c.world.state["ship_integrity"]["integrity"], integrity)

    def test_charter_and_cede_are_different_settlements(self):
        chartered, ceded = campaign(2), campaign(2)
        for c, op in ((chartered, "charter_steward"), (ceded, "cede_subsystem")):
            state = self.activate(c)
            for _ in range(3):
                c.world.tick += 1
                incidents_ai.annual_tick(c)
            voyage.open_incident(c, "ai_steward")
            commit(c, [op])
            self.assertTrue(c.play["incident"]["resolved"])
        a, b = chartered.world.state[incidents_ai.SLICE], ceded.world.state[incidents_ai.SLICE]
        self.assertEqual((a["status"], b["status"]), ("aligned", "ceded"))
        self.assertGreater(b["sequestered"]["food_kg"], a["sequestered"]["food_kg"])  # charter returned 60%
        self.assertGreater(chartered.summary()["mandate"], ceded.summary()["mandate"])
        food = ceded.world.state["resources"]["food_kg"]
        ceded.world.tick += 1
        incidents_ai.annual_tick(ceded)
        self.assertLess(ceded.world.state["resources"]["food_kg"], food)  # still taking, reduced


# === Standing policies: steering between wakes =============================
class PolicyTests(unittest.TestCase):
    def test_policy_sets_persists_and_applies_across_cryo_years(self):
        c = campaign(4)
        voyage.open_incident(c, "review")
        commit(c, ["policy_apothecary"])
        self.assertIn("policy_apothecary", c.play["policies"])
        self.assertTrue(c.play["incident"]["resolved"])
        twin = campaign(4)
        with patch.object(c, "save", lambda: None), patch.object(twin, "save", lambda: None):
            for _ in range(3):
                voyage.travel_tick(c)
                voyage.travel_tick(twin)
        self.assertGreater(c.summary()["medicine_years"], twin.summary()["medicine_years"])
        ledger = c.play["policy_ledger"]
        self.assertEqual(len(ledger), 3)
        self.assertLess(ledger[-1]["applied"]["policy_apothecary"]["food_kg"], 0)  # visible cost
        with tempfile.TemporaryDirectory(prefix="proxima-incidents-test-") as temp:
            with patch.object(engine, "ROOT", Path(temp)):
                c.save()
                restored = engine.Campaign.load(Path(temp) / "state" / f"{c.id}.json")
        voyage.ensure(restored)
        self.assertIn("policy_apothecary", restored.play["policies"])
        with patch.object(c, "save", lambda: None), patch.object(restored, "save", lambda: None):
            voyage.travel_tick(c)
            voyage.travel_tick(restored)
        self.assertEqual(c.summary()["medicine_years"], restored.summary()["medicine_years"])

    def test_policy_cap_and_rescind(self):
        c = campaign()
        voyage.open_incident(c, "review")
        c.play["policies"] = {p: {"since": 0} for p in ("policy_watch", "policy_assembly", "policy_maintenance")}
        with self.assertRaises(ValueError):
            voyage.validate_plan(c, {"operations": ["policy_apothecary"]})
        commit(c, ["policy_watch"])  # committing an active doctrine rescinds it
        self.assertNotIn("policy_watch", c.play["policies"])

    def test_policies_survive_in_available_operations_for_domain_incidents(self):
        c = campaign()
        c.world.state["governance"]["legitimacy"] = .35
        c.world.tick = 12
        incidents_politics.annual_tick(c)
        voyage.open_incident(c, "uprising")
        ops = voyage.available_operations(c)
        self.assertIn("policy_assembly", ops)
        self.assertIn("concede", ops)
        self.assertIn("hold", ops)
        self.assertNotIn("isolate", ops)
        self.assertNotIn("purge_network", ops)


# === Wake mix: medical must not dominate ===================================
RESOLVE = {
    "health": [["medical"]], "food": [["ration"], ["gardens"]],
    "repair": [["maintenance"], ["slow"]], "trust": [["charter"], ["rest"]], "review": [["hold"]],
    "uprising": [["concede"], ["crackdown"]], "crime": [["purge_network"], ["turn_network"]],
    "cascade": [["overhaul"], ["strip_nonessential"], ["maintenance", "slow"]],
    "ai_steward": [["partition_core"], ["charter_steward"]],
}
POLICY_FOR = {"health": "policy_apothecary", "uprising": "policy_assembly", "trust": "policy_assembly", "crime": "policy_watch", "cascade": "policy_maintenance", "repair": "policy_maintenance"}


def play_out(c, kind):
    """Resolve a locked incident the way a competent admiral offline would."""
    scripts = list(RESOLVE.get(kind, [["hold"]]))
    for attempt in range(4):
        for ops in scripts[min(attempt, len(scripts) - 1):] + [["hold"]]:
            ops = list(ops)
            policy = POLICY_FOR.get(kind)
            if policy and policy not in c.play["policies"] and len(c.play["policies"]) < incidents_core.MAX_POLICIES and ops != ["hold"]:
                ops.append(policy)
            try:
                commit(c, ops)
                break
            except ValueError:
                continue
        else:
            c.play["mode"] = "ready"
            return
        if c.play["mode"] == "ready" or c.status != "in_flight":
            return
    c.play["mode"] = "ready"


class WakeMixTests(unittest.TestCase):
    def test_seed_sweep_medical_no_longer_dominates(self):
        total = collections.Counter()
        for seed in range(1, 7):
            c = campaign(seed)
            last_wake = -10
            with patch.object(c, "save", lambda: None):
                while c.world.tick < 80 and c.status == "in_flight":
                    voyage.travel_tick(c)
                    if c.status != "in_flight":
                        break
                    kind = voyage.urgent(c)
                    if kind and c.world.tick - last_wake >= 2:  # cryo's own wake gate
                        total[kind] += 1
                        last_wake = c.world.tick
                        voyage.open_incident(c, kind)
                        play_out(c, kind)
            self.assertEqual(c.status, "in_flight", f"seed {seed} should survive 80 managed years")
        wakes = sum(total.values())
        self.assertGreaterEqual(wakes, 30)
        self.assertLessEqual(total.get("health", 0) / wakes, .5, f"medical still dominates: {dict(total)}")
        self.assertGreaterEqual(len(total), 4, f"wake variety collapsed: {dict(total)}")
        self.assertLessEqual(wakes, 6 * 40, "wake cadence left no cryo room")

    def test_health_cooldown_stops_the_old_medicine_loop(self):
        c = campaign()
        c.world.state["resources"]["medicine_kg"] = 0
        voyage.open_incident(c, "health")
        commit(c, ["hold"])  # explicitly accepted risk
        self.assertIsNone(voyage.urgent(c))  # not renagged the same year
        c.world.tick += 2
        self.assertEqual(voyage.urgent(c), "health")  # revisited after the cooldown


# === Schema and no-op guarantees ===========================================
class SchemaTests(unittest.TestCase):
    def test_every_domain_kind_has_a_wellformed_incident(self):
        c = campaign()
        for kind in incidents_core.DOMAINS:
            info = voyage.open_incident(c, kind)
            for key in ("title", "kind", "goal", "evidence", "room", "id"):
                self.assertIn(key, info)
            self.assertEqual(info["domain"], kind)
            self.assertTrue(info["evidence"], kind)
            self.assertIn("note", info["evidence"][-1])

    def test_domain_summaries_ride_barriers_for_astra(self):
        c = campaign()
        b = voyage.barriers(c)
        self.assertEqual(set(b["domains"]), set(incidents_core.DOMAINS))
        self.assertEqual(b["policies"]["limit"], incidents_core.MAX_POLICIES)

    def test_annual_tick_never_touches_population_counts(self):
        c = campaign(5)
        with patch.object(c, "save", lambda: None):
            for _ in range(20):
                before = alive(c)
                deaths = c.world.state["population"]["deaths_total"]
                for module in incidents_core.DOMAINS.values():
                    module.annual_tick(c)
                self.assertEqual(alive(c), before)
                self.assertEqual(c.world.state["population"]["deaths_total"], deaths)
                voyage.travel_tick(c)


if __name__ == "__main__":
    unittest.main(verbosity=2)
