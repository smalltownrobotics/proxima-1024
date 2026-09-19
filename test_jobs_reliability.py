"""Job-machinery reliability tests: crash injection, watchdog walls, token
rotation recovery, cryo-block legibility, and stress-shaped lifecycle checks.

All offline: no provider calls, no writes under state/. These encode the
2026-09-18 issue #1 (job desync / eternal spinner) and issue #2 (cryo-block
legibility) contracts so regressions fail loudly.
"""
import copy
import json
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import sys

import engine

# engine prepends sim_engine/ for its imports. Pin the repository root first
# so "server" resolves consistently regardless of test-discovery import order.
sys.path.insert(0, str(engine.ROOT))
import server  # noqa: E402
import voyage  # noqa: E402


OPTIONS = {"crew": 200, "ship": "ship_modular_cluster", "drive": "prop_fusion_continuous", "speed": .05, "launch_year": 2250, "scenario": "outbreak"}


def wait_terminal(jid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = server.JOBS.get(jid)
        if entry and entry["status"] != "running":
            return entry
        time.sleep(.01)
    raise AssertionError(f"Job {jid} never reached a terminal status: {server.JOBS.get(jid)}")


def busy_free():
    """True if the single job slot can be acquired (and immediately returned)."""
    if server.BUSY.acquire(blocking=False):
        server.BUSY.release()
        return True
    return False


class StubCampaign:
    """Minimal campaign for job-machinery tests; never touches real state."""
    def __init__(self, fail_save=False, fail_public=False):
        self.id = "ab" * 8
        self.messages = []
        self.fail_save, self.fail_public = fail_save, fail_public
        self.saved = 0

    def save(self):
        if self.fail_save:
            raise IOError("disk full")
        self.saved += 1

    def public(self):
        if self.fail_public:
            raise RuntimeError("public failed")
        return {"id": self.id, "play": None}


class JobTerminalTests(unittest.TestCase):
    """Issue #1a: every job thread must reach a terminal status and free BUSY."""

    def setUp(self):
        server.JOBS.clear()
        self.assertTrue(busy_free(), "test started with a leaked BUSY lock")

    def test_crash_injection_reaches_failed(self):
        for exc in (RuntimeError("boom"), ValueError("bad input"), KeyError("k"), ZeroDivisionError()):
            with patch.object(server, "generate", side_effect=exc):
                jid = server.job("generate", {})
                entry = wait_terminal(jid)
            self.assertEqual(entry["status"], "failed")
            self.assertTrue(entry["error"])
            self.assertTrue(busy_free(), f"BUSY leaked after {type(exc).__name__}")
        # No credential/internal details for non-Value/Runtime errors.
        self.assertIn("Ship operation stopped", entry["error"])

    def test_terminal_even_when_error_path_save_and_public_fail(self):
        # Before the fix, campaign.save() raising inside the except handler left
        # the job "running" forever — the exact shape of an eternal spinner.
        stub = StubCampaign(fail_save=True, fail_public=True)
        with patch.object(server, "get_campaign", return_value=stub), \
             patch.object(server, "ensure", return_value={"revision": 0}), \
             patch.object(server, "astra", side_effect=RuntimeError("provider down")):
            jid = server.job("talk", {"campaign_id": stub.id, "message": "status?"})
            entry = wait_terminal(jid)
        self.assertEqual(entry["status"], "failed")
        self.assertIsNone(entry.get("campaign"))
        self.assertTrue(busy_free())

    def test_success_path_annotates_and_releases(self):
        campaign_payload = {"id": "cd" * 8, "play": {"mode": "ready", "incident": None, "revision": 3, "last_decision": None}, "outbreak": None}
        with patch.object(server, "get_campaign", return_value=StubCampaign()), \
             patch.object(server, "ensure", return_value={"revision": 0}), \
             patch.object(server, "astra", return_value="All quiet."):
            jid = server.job("talk", {"campaign_id": "cd" * 8, "message": "status?"})
            entry = wait_terminal(jid)
        self.assertEqual(entry["status"], "done")
        self.assertTrue(busy_free())
        # annotate_campaign runs on every result campaign (None reason when unblocked).
        annotated = server.annotate_campaign(copy.deepcopy(campaign_payload))
        self.assertIn("cryo_blocked_reason", annotated)
        self.assertIsNone(annotated["cryo_blocked_reason"])

    def test_busy_slot_is_exclusive_and_recovers(self):
        release = threading.Event()
        def slow(_options):
            release.wait(5)
            raise RuntimeError("done stalling")
        with patch.object(server, "generate", side_effect=slow):
            jid = server.job("generate", {})
            refused = 0
            for _ in range(8):
                try:
                    server.job("generate", {})
                except ValueError:
                    refused += 1
            self.assertEqual(refused, 8)
            release.set()
            wait_terminal(jid)
        self.assertTrue(busy_free())


class WatchdogTests(unittest.TestCase):
    """Issue #1b: a hung job becomes failed and releases cleanly; the zombie
    worker can neither flip the status back nor double-release the lock."""

    def setUp(self):
        server.JOBS.clear()
        self.assertTrue(busy_free())

    def test_stalled_job_is_failed_and_released(self):
        release = threading.Event()
        def hang(_options):
            release.wait(10)
            return StubCampaign()
        with patch.object(server, "generate", side_effect=hang):
            jid = server.job("generate", {})
            time.sleep(.1)  # let the worker emit its startup progress pulse
            # Nothing expired yet.
            self.assertEqual(server.watchdog_scan(), [])
            # Simulate a stall longer than the progress wall.
            server.JOBS[jid]["updated"] = time.time() - server.JOB_STALL - 1
            expired = server.watchdog_scan()
            self.assertEqual(expired, [jid])
            entry = server.JOBS[jid]
            self.assertEqual(entry["status"], "failed")
            self.assertTrue(entry.get("watchdog"))
            self.assertIn("safety window", entry["error"])
            self.assertTrue(busy_free(), "watchdog failed to release BUSY")
            # A new job can start while the zombie still runs.
            with patch.object(server, "generate", side_effect=RuntimeError("next")):
                jid2 = server.job("generate", {})
                wait_terminal(jid2)
            self.assertTrue(busy_free())
            # Zombie finishes late: terminal status must not change, lock must not double-release.
            release.set()
            time.sleep(.15)
            self.assertEqual(server.JOBS[jid]["status"], "failed")
            self.assertTrue(busy_free())

    def test_absolute_cap_expires_job(self):
        release = threading.Event()
        with patch.object(server, "generate", side_effect=lambda _o: release.wait(10)):
            jid = server.job("generate", {})
            server.JOBS[jid]["started"] = time.time() - server.JOB_CAP["generate"] - 1
            # Progress is fresh, but the absolute cap still applies.
            server.JOBS[jid]["updated"] = time.time()
            self.assertEqual(server.watchdog_scan(), [jid])
            release.set()
        wait_terminal(jid)
        self.assertTrue(busy_free())


class HttpContractTests(unittest.TestCase):
    """Token rotation and restart-recovery contracts the client relies on."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def request(self, path, data=None, token=None):
        headers = {}
        if data is not None:
            headers = {"Content-Type": "application/json", "Origin": self.origin, "X-Proxima-Token": token or ""}
        req = urllib.request.Request(self.origin + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_stale_token_403_then_bootstrap_recovers(self):
        server.JOBS.clear()
        # Stale token (server restarted): the mutation must be refused, not queued.
        status, body = self.request("/api/talk", {"campaign_id": "ab" * 8, "message": "hello"}, token="stale-token")
        self.assertEqual(status, 403)
        self.assertTrue(busy_free(), "a 403'd request must not hold the job slot")
        # Client recovery path: re-bootstrap, then retry once with the fresh token.
        status, body = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(body["token"], server.TOKEN)
        self.assertIsNone(body["active_job"])
        with patch.object(server, "get_campaign", return_value=StubCampaign()), \
             patch.object(server, "ensure", return_value={"revision": 0}), \
             patch.object(server, "astra", return_value="Recovered."):
            status, body = self.request("/api/talk", {"campaign_id": "ab" * 8, "message": "hello"}, token=body["token"])
            self.assertEqual(status, 202)
            entry = wait_terminal(body["job_id"])
        self.assertEqual(entry["status"], "done")

    def test_restarted_server_404s_unknown_job(self):
        # pollJob's restore path depends on a clean 404 for a forgotten job id.
        status, body = self.request("/api/jobs/deadbeefdeadbeef")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_job_endpoint_never_leaks_internal_keys(self):
        release = threading.Event()
        with patch.object(server, "generate", side_effect=lambda _o: release.wait(5)):
            jid = server.job("generate", {})
            status, body = self.request("/api/jobs/" + jid)
            self.assertEqual(status, 200)
            self.assertFalse([k for k in body if k.startswith("_")], "internal keys leaked to the client")
            release.set()
            wait_terminal(jid)

    def test_bootstrap_advertises_running_job(self):
        release = threading.Event()
        with patch.object(server, "generate", side_effect=lambda _o: release.wait(5)):
            jid = server.job("generate", {})
            status, body = self.request("/api/bootstrap")
            self.assertEqual(status, 200)
            self.assertEqual(body["active_job"]["id"], jid)
            release.set()
            wait_terminal(jid)
        status, body = self.request("/api/bootstrap")
        self.assertIsNone(body["active_job"])


class CryoBlockedReasonTests(unittest.TestCase):
    """Issue #2: the campaign payload states why cryo is refused, numerically."""

    def test_annotation_rules(self):
        base = {"id": "aa" * 8, "outbreak": {"active": True, "infected": 41}, "play": {"mode": "awake", "revision": 5, "incident": {"kind": "outbreak", "resolved": False}, "last_decision": {"revision": 5}}}
        blocked = server.annotate_campaign(copy.deepcopy(base))
        self.assertEqual(blocked["cryo_blocked_reason"], "41 still infected · cryo locked until zero")
        # Pre-response (no committed decision at this revision): no reason yet.
        pre = copy.deepcopy(base)
        pre["play"]["last_decision"] = {"revision": 4}
        self.assertIsNone(server.annotate_campaign(pre)["cryo_blocked_reason"])
        # Resolved incident: no reason.
        done = copy.deepcopy(base)
        done["play"]["incident"]["resolved"] = True
        self.assertIsNone(server.annotate_campaign(done)["cryo_blocked_reason"])
        # Non-outbreak unresolved incident after a response: generic reason.
        other = copy.deepcopy(base)
        other["play"]["incident"] = {"kind": "repair", "resolved": False}
        other["outbreak"] = None
        self.assertIn("cryo locked", server.annotate_campaign(other)["cryo_blocked_reason"])
        # v1 payloads (play None) and non-dicts pass through untouched.
        self.assertEqual(server.annotate_campaign({"play": None}), {"play": None})
        self.assertIsNone(server.annotate_campaign(None))

    def test_real_commit_produces_consistent_reason(self):
        c = engine.Campaign(engine.make_config(OPTIONS))
        c.world.state["bridge_outbreak"]["onset_year"] = 0
        import outbreak
        self.assertTrue(outbreak.start_if_due(c))
        voyage.open_incident(c, "outbreak")
        reactions = {p["id"]: {"probabilities": {"support": .8, "question": .15, "oppose": .05}, "choice": "support"} for p in c.crew()}
        with patch.object(c, "save"):
            voyage.commit(c, "Isolate the affected decks", {"operations": ["isolate"]}, reactions, {"summary": {}})
        payload = server.annotate_campaign(c.public())
        summary = payload["outbreak"]
        if summary["active"]:
            self.assertEqual(payload["cryo_blocked_reason"], f"{summary['infected']:,} still infected · cryo locked until zero")
            self.assertEqual(payload["play"]["mode"], "awake")
        else:
            self.assertIsNone(payload["cryo_blocked_reason"])
            self.assertEqual(payload["play"]["mode"], "ready")


class LifecycleStressTests(unittest.TestCase):
    """Stress-shaped: many sequential jobs, pruning, and per-job terminality."""

    def setUp(self):
        server.JOBS.clear()
        self.assertTrue(busy_free())

    def test_many_jobs_always_terminal_and_pruned(self):
        outcomes = {"done": 0, "failed": 0}
        with patch.object(server, "get_campaign", return_value=StubCampaign()), \
             patch.object(server, "ensure", return_value={"revision": 0}):
            for i in range(3 * server.JOBS_KEEP):
                if i % 3 == 2:
                    patcher = patch.object(server, "astra", side_effect=RuntimeError("flaky"))
                else:
                    patcher = patch.object(server, "astra", return_value="ok")
                with patcher:
                    jid = server.job("talk", {"campaign_id": "ab" * 8, "message": "ping"})
                    outcomes[wait_terminal(jid)["status"]] += 1
                self.assertTrue(busy_free(), f"BUSY leaked at job {i}")
        self.assertEqual(sum(outcomes.values()), 3 * server.JOBS_KEEP)
        self.assertGreater(outcomes["failed"], 0)
        self.assertLessEqual(len(server.JOBS), server.JOBS_KEEP + 1, "JOBS map is not pruned")

    def test_concurrent_submissions_admit_exactly_one(self):
        release = threading.Event()
        admitted, refused = [], []
        def submit():
            try:
                admitted.append(server.job("generate", {}))
            except ValueError:
                refused.append(1)
        with patch.object(server, "generate", side_effect=lambda _o: release.wait(5)):
            threads = [threading.Thread(target=submit) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(admitted), 1)
            self.assertEqual(len(refused), 9)
            release.set()
            wait_terminal(admitted[0])
        self.assertTrue(busy_free())


class ClientContractTests(unittest.TestCase):
    """Tripwires for the app.js reliability patterns fixed for issue #1."""

    def setUp(self):
        self.js = (engine.ROOT / "public/app.js").read_text()

    def test_frame_chain_is_never_extended_unprotected(self):
        # Every chain extension must go through chainFrames (which .catch()es),
        # otherwise one bad frame step poisons the chain into permanent rejection.
        raw = [m for m in re.findall(r"state\.frameChain\s*=\s*state\.frameChain\.then[^\n]*", self.js) if ".catch(" not in m]
        self.assertFalse(raw, f"unprotected frameChain extension: {raw}")
        self.assertIn("function chainFrames(", self.js)

    def test_no_raw_await_on_frame_chain(self):
        # `await state.frameChain` throws forever once the chain is rejected;
        # all waits must use the capped, never-rejecting awaitFrames().
        self.assertNotIn("await state.frameChain", self.js)
        self.assertIn("function awaitFrames(", self.js)

    def test_poll_gives_up_and_offers_restore(self):
        self.assertIn("noticeRestore(", self.js)
        self.assertIn("360000", self.js)  # six-minute no-progress wall

    def test_cryo_block_reason_is_rendered(self):
        self.assertIn("cryo_blocked_reason", self.js)
        self.assertIn('id="cryo-block-reason"', self.js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
