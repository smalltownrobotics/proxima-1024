"""Offline UI-verification server: real bridge UI + job machinery, mocked models.

Serves the actual public/ assets and the real server.py request/job pipeline on
a private loopback port, with every provider call mocked and all saves
redirected to a temp directory (state/ is never touched). Used by the
reliability browser checks; never run this as the production service.

Usage:
    .venv/bin/python stress/mock_server.py --port 8931 --slow-generate 6 --seed-campaign
Prints one JSON line {"port":..., "campaign":..., "reason":...} then serves.
"""
import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE))

import offline_mocks  # noqa: E402

offline_mocks.install()

import engine  # noqa: E402
sys.path.insert(0, str(BRIDGE))  # engine prepends sim_engine/ for its imports
import server  # noqa: E402
import voyage  # noqa: E402
import outbreak  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


def redirect_state():
    temp = Path(tempfile.mkdtemp(prefix="proxima-mock-ui-"))
    (temp / "state").mkdir()
    (temp / "public").symlink_to(BRIDGE / "public")
    engine.ROOT = server.ROOT = temp
    return temp


def seed_blocked_campaign():
    """An awake campaign with an unresolved outbreak after a committed response
    (hold: the plague runs 28 unmitigated days), i.e. the issue #2 state."""
    options = {"crew": 400, "ship": "ship_modular_cluster", "drive": "prop_fusion_continuous", "speed": .05, "launch_year": 2250, "scenario": "outbreak"}
    for attempt in range(6):
        c = engine.Campaign(engine.make_config(options))
        c.world.state["bridge_outbreak"]["onset_year"] = 0
        assert outbreak.start_if_due(c)
        voyage.open_incident(c, "outbreak")
        reactions = {p["id"]: {"probabilities": {"support": .7, "question": .2, "oppose": .1}, "confidence": .9, "choice": "support"} for p in c.crew()}
        voyage.commit(c, "Hold the current response", {"operations": ["hold"]}, reactions, {"summary": {"people": len(reactions), "seconds": .01, "model": "mock-jev", "year": 0, "action": "hold", "mean_distribution": {"support": .7, "question": .2, "oppose": .1}}})
        if (outbreak.summary(c) or {}).get("status") == "active":
            server.CAMPAIGNS[c.id] = c
            c.save()
            return c
    raise RuntimeError("could not seed an active post-response outbreak")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--slow-generate", type=float, default=0, help="extra seconds added to generate jobs (reload-window testing)")
    parser.add_argument("--seed-campaign", action="store_true")
    args = parser.parse_args()

    redirect_state()
    server.readiness = lambda: {"astra": {"configured": True, "model": "mock"}, "jev": {"configured": True, "model": "mock"}}
    if args.slow_generate:
        real_generate = server.generate
        def slow_generate(options):
            time.sleep(args.slow_generate)
            return real_generate(options)
        server.generate = slow_generate

    info = {"port": args.port, "campaign": None, "reason": None}
    if args.seed_campaign:
        c = seed_blocked_campaign()
        info["campaign"] = c.id
        info["reason"] = server.annotate_campaign(c.public())["cryo_blocked_reason"]

    threading.Thread(target=server._watchdog_loop, daemon=True).start()
    # Bind BEFORE printing: the info line is the harness's ready signal, so a
    # port conflict must fail before a client starts driving the browser.
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), server.Handler)
    print(json.dumps(info), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
