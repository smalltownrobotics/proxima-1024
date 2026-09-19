"""Loopback-only Proxima Bridge server: allowlisted assets, jobs, private saves.

Run with .venv/bin/python server.py. This process never serves its working
directory. Every mutating request needs a same-origin session token.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import json
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from engine import ROOT, Campaign, catalog, generate, SITUATIONS, ROOMS
from providers import readiness, converse
from voyage import ensure, barriers, urgent, open_incident
from live_voyage import decide, cryo, astra

# === In-memory job coordination ===
TOKEN = secrets.token_urlsafe(32)
CAMPAIGNS, JOBS = {}, {}
BUSY = threading.Lock()
# JOBS_GUARD orders terminal writes and guarantees BUSY is released exactly once
# per job, even when the watchdog and a slow worker thread race (issue #1).
JOBS_GUARD = threading.Lock()
JOBS_KEEP = 40
# Reliability walls (seconds). STALL is time since the last progress update; CAP
# is absolute wall clock per kind. A job past either is failed and released so a
# hung provider call can never hold the single BUSY slot forever. The timed-out
# worker is not forcibly killed; a later write can race a new same-campaign job.
JOB_STALL = 600
JOB_CAP = {"generate": 900, "talk": 900, "chat": 900, "consult": 1800, "decide": 1800, "decision": 2400, "cryo": 3600, "agent": 2400}  # C: agent = multi-minute Claude loop (live brief ≈ 350 s)
WATCHDOG_INTERVAL = 5
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css"), "/scene.js": ("scene.js", "text/javascript"), "/assets/stars.json": ("assets/stars.json", "application/json"), "/assets/sky-targets.json": ("assets/sky-targets.json", "application/json"), "/assets/planet.glb": ("assets/planet.glb", "model/gltf-binary"), "/assets/ship.glb": ("assets/ship.glb", "model/gltf-binary")}
# Explicit visual asset allowlist: never expose a source or state directory.
for filename in ("cruiser.glb", "freighter.glb", "spine.glb", "worn-hull.jpg", "rocky-world.jpg", "deep-space.jpg"):
    relative = "assets/industrial/" + filename
    ASSETS["/" + relative] = (relative, "model/gltf-binary" if filename.endswith(".glb") else "image/jpeg")


def get_campaign(cid):
    if not isinstance(cid, str) or not re.fullmatch(r"[a-f0-9]{16}", cid):
        raise ValueError("Invalid expedition identifier.")
    if cid not in CAMPAIGNS:
        path = ROOT / "state" / (cid + ".json")
        if not path.is_file():
            raise ValueError("Saved expedition not found.")
        CAMPAIGNS[cid] = Campaign.load(path)
        # No hidden autoplay after a process restart midway through cryo.
        if CAMPAIGNS[cid].play and CAMPAIGNS[cid].play["mode"] == "cryo":
            interrupted = CAMPAIGNS[cid]
            kind = urgent(interrupted)
            if kind:
                open_incident(interrupted, kind)
            else:
                interrupted.play["mode"] = "ready"
    ensure(CAMPAIGNS[cid])
    return CAMPAIGNS[cid]


def annotate_campaign(payload):
    """Issue #2: make an unresolved post-response cryo block explicit and numeric.

    Pure function of the public payload; attached at every boundary that returns
    a campaign so the client never has to infer why Return-to-cryo is refused.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("play"), dict):
        return payload
    play = payload["play"]
    incident = play.get("incident") or {}
    last = play.get("last_decision") or {}
    reason = None
    if play.get("mode") == "awake" and incident and not incident.get("resolved") and last and last.get("revision") == play.get("revision"):
        epidemic = payload.get("outbreak") or {}
        if incident.get("kind") == "outbreak" and epidemic.get("active"):
            reason = f"{epidemic.get('infected', 0):,} still infected · cryo locked until zero"
        else:
            reason = "Incident unresolved · cryo locked until repaired or its risk is explicitly accepted"
    payload["cryo_blocked_reason"] = reason
    return payload


def _finish(jid, **fields):
    """Idempotent terminal write plus exactly-once BUSY release.

    First terminal writer wins (worker or watchdog); the loser's write is
    ignored and the lock is never double-released or leaked.
    """
    with JOBS_GUARD:
        entry = JOBS.get(jid, {})
        if entry.get("status") == "running":
            entry.update(fields)
            entry["updated"] = time.time()
        if entry.pop("_busy_held", False):
            BUSY.release()


def watchdog_scan(now=None):
    """Fail any running job past its stall/absolute wall. Safe to call anytime."""
    now = now or time.time()
    with JOBS_GUARD:
        expired = [j["id"] for j in JOBS.values() if j["status"] == "running"
                   and (now - j.get("updated", j["started"]) > JOB_STALL or now - j["started"] > JOB_CAP.get(j["kind"], 1800))]
    for jid in expired:
        _finish(jid, status="failed", watchdog=True, error="This ship operation ran past its safety window and was released. The last saved state is intact; no order was silently repeated.")
    return expired


def _watchdog_loop():
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        try:
            watchdog_scan()
        except Exception:
            pass  # The watchdog must outlive any single bad scan.


def job(kind, data):
    if not BUSY.acquire(blocking=False):
        raise ValueError("A ship operation is already running. Let it finish first.")
    try:
        jid = secrets.token_hex(8)
        with JOBS_GUARD:
            # Prune old terminal entries so JOBS cannot grow without bound.
            for old in sorted((j for j in JOBS.values() if j["status"] != "running"), key=lambda j: j["started"])[:-JOBS_KEEP or None]:
                JOBS.pop(old["id"], None)
            JOBS[jid] = {"id": jid, "kind": "decision" if kind == "decision_text" else kind, "campaign_id": data.get("campaign_id"), "status": "running", "phase": "Preparing expedition systems", "started": time.time(), "updated": time.time(), "_busy_held": True}
    except BaseException:
        BUSY.release()
        raise
    def progress(phase):
        if isinstance(phase, dict):
            if "campaign" in phase:
                annotate_campaign(phase["campaign"])
            JOBS[jid].update(phase)
        else:
            JOBS[jid]["phase"] = phase
        JOBS[jid]["updated"] = time.time()
    def work():
        campaign = None
        try:
            if kind == "generate":
                progress("Generator · running six independent eight-year commissioning trials")
                campaign = generate(data.get("options", {}))
                ensure(campaign)
                campaign.messages = clean_conversation(data.get("conversation", []))
                CAMPAIGNS[campaign.id] = campaign
                campaign.save()
                result = {"campaign": campaign.public()}
            elif kind in ("decision_text", "cryo", "talk", "consult"):
                campaign = get_campaign(data.get("campaign_id"))
                p = ensure(campaign)
                if kind not in ("talk", "consult") and p["revision"] != data.get("revision"):
                    raise ValueError("The ship has changed. Reload its current state before issuing another order.")
                if kind == "decision_text":
                    decision = str(data.get("decision", "")).strip()
                    if not 8 <= len(decision) <= 1600:
                        raise ValueError("Describe your decision in 8–1,600 characters.")
                    result = decide(campaign, decision, progress)
                elif kind == "cryo":
                    result = cryo(campaign, progress)
                elif kind == "consult":
                    from advisory import consult
                    if campaign.status != "in_flight" or p["mode"] not in ("awake", "ready"):
                        raise ValueError("The admiral must be awake to convene a council.")
                    question = str(data.get("question", "")).strip()
                    if not 8 <= len(question) <= 1600:
                        raise ValueError("Ask the council a question of 8–1,600 characters.")
                    group = data.get("group", "medical")
                    progress("Jev · convening the admiral's council")
                    report = consult(campaign, group, question, progress, person_id=data.get("person_id"))
                    p["consultations"] = (p["consultations"] + [report["consultation"]])[-6:]
                    p["advisory_audits"].append(report["audit"])
                    campaign.save()
                    result = {"campaign": campaign.public(), "consultation": report["consultation"], "read_only": True}
                else:
                    message = str(data.get("message", "")).strip()
                    if not 1 <= len(message) <= 1600:
                        raise ValueError("Enter a question of 1–1,600 characters.")
                    progress("Astra is considering your question")
                    answer = astra(campaign, message)
                    result = {"campaign": campaign.public(), "text": answer}
            elif kind == "agent":
                # === C: resident Claude ship-intelligence agent (read-only, advisory). ===
                # claude_agent owns snapshotting, streaming and persistence via the
                # campaign's normal save path; it can never mutate world/RNG/revision.
                from claude_agent import run_agent_job
                campaign = get_campaign(data.get("campaign_id"))
                ensure(campaign)
                result = run_agent_job(campaign, data, progress)
            elif kind == "chat":
                campaign = get_campaign(data["campaign_id"]) if data.get("campaign_id") else None
                if campaign and campaign.play and campaign.play.get("version") == 2:
                    raise ValueError("Reload this old page to use the new voyage conversation interface.")
                if campaign and data.get("focus") in {r["id"] for r in ROOMS}:
                    campaign.focus = data["focus"]
                if campaign and data.get("boarding") and campaign.world.tick == 0 and not campaign.history:
                    campaign.messages = clean_conversation(data.get("conversation", []))
                message = str(data.get("message", "")).strip()
                if not message or len(message) > 2500:
                    raise ValueError("Enter a message of 1–2,500 characters.")
                result = converse(campaign, message, progress, options=data.get("options"), conversation=clean_conversation(data.get("conversation", [])))
            elif kind == "decide":
                campaign = get_campaign(data.get("campaign_id"))
                if campaign.play and campaign.play.get("version") == 2:
                    raise ValueError("This voyage uses the new decision interface. Reload this old page before continuing.")
                if campaign.world.tick != int(data.get("year", -1)):
                    raise ValueError("The expedition has already moved forward. Refresh the ship state before choosing.")
                action = data.get("action_id")
                if action not in SITUATIONS[campaign.situation]["actions"] or campaign.status != "in_flight":
                    raise ValueError("That order is not available now.")
                years = int(data.get("years", 1))
                if years not in (1, 3, 5):
                    raise ValueError("Choose 1, 3, or 5 years.")
                result = converse(campaign, "I authorize this order. Run it, show me how the crew responds, and tell me what happens next.", progress, action=action, years=years)
            else:
                raise ValueError("Unknown operation.")
            if isinstance(result, dict):
                annotate_campaign(result.get("campaign"))
            _finish(jid, status="done", phase="Ready", result=result)
        except Exception as exc:
            # No credentials or network request objects in errors or logs.
            error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f"Ship operation stopped ({type(exc).__name__}). State has been preserved."
            payload = None
            try:
                # The terminal status must land even if save/public themselves
                # fail here; a job stuck "running" forever is the worse outcome.
                if campaign:
                    campaign.save()
                    payload = annotate_campaign(campaign.public())
            except Exception:
                payload = None
            _finish(jid, status="failed", error=error, campaign=payload)
        finally:
            # Belt-and-braces: _finish is idempotent, so an unexpected exit path
            # (e.g. an exception inside the except handler) still releases BUSY.
            _finish(jid, status="failed", error="Ship operation stopped. State has been preserved.")
    threading.Thread(target=work, daemon=True).start()
    return jid


def clean_conversation(value):
    if not isinstance(value, list):
        raise ValueError("Invalid conversation.")
    return [{"role": m["role"], "text": str(m["text"])[:2500]} for m in value[-12:] if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str)]


# === HTTP boundary ===
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, value, content_type="application/json"):
        body = json.dumps(value).encode() if content_type == "application/json" and not isinstance(value, bytes) else value
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.host_ok():
            return self.respond(403, {"error": "Loopback host required."})
        path = urlparse(self.path).path
        try:
            if path in ASSETS:
                name, kind = ASSETS[path]
                return self.respond(200, (ROOT / "public" / name).read_bytes(), kind)
            if path == "/api/bootstrap":
                saves = []
                for p in sorted((ROOT / "state").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
                    d = json.loads(p.read_text())
                    saves.append({"id": d["id"], "name": d["config"]["name"], "year": d["tick"], "destination": d["config"]["mission"]["destination_name"], "version": 2 if d.get("play") else 1})
                active = next(({k: j[k] for k in ("id", "kind", "campaign_id")} for j in JOBS.values() if j["status"] == "running"), None)
                return self.respond(200, {"catalog": catalog(), "providers": readiness(), "token": TOKEN, "saves": saves, "active_job": active})
            if path.startswith("/api/jobs/"):
                j = JOBS.get(path.rsplit("/", 1)[1])
                return self.respond(200 if j else 404, {k: v for k, v in j.items() if not k.startswith("_")} if j else {"error": "Operation not found."})
            if path.startswith("/api/campaign/"):
                return self.respond(200, annotate_campaign(get_campaign(path.rsplit("/", 1)[1]).public()))
            return self.respond(404, {"error": "Not found."})
        except (ValueError, FileNotFoundError):
            return self.respond(404, {"error": "Expedition or asset not found."})

    def do_POST(self):
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if not self.host_ok() or self.headers.get("Origin", expected) != expected or self.headers.get("X-Proxima-Token") != TOKEN:
            return self.respond(403, {"error": "Same-origin session token required."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.respond(415, {"error": "JSON required."})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length < 65000:
                raise ValueError("Request too large or empty.")
            data = json.loads(self.rfile.read(length))
            kind = {"/api/generate": "generate", "/api/chat": "chat", "/api/decide": "decide", "/api/decision": "decision_text", "/api/cryo": "cryo", "/api/talk": "talk", "/api/consult": "consult", "/api/agent": "agent"}.get(self.path)
            if not kind:
                return self.respond(404, {"error": "Unknown operation."})
            return self.respond(202, {"job_id": job(kind, data)})
        except (ValueError, TypeError, KeyError):
            return self.respond(400, {"error": "Invalid request, or a ship operation is already running."})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8924)
    args = parser.parse_args()
    threading.Thread(target=_watchdog_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Proxima Bridge listening at http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
