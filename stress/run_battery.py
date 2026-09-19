"""Overnight stress battery: headless full-loop campaigns with mocked providers.

Runs commission → cryo years → wakes → decisions → resolutions → repeat across
many seeds, entirely offline, and asserts on every loop:
  - conservation: alive == flags == initial + births − deaths; stores >= 0;
    outbreak case bookkeeping matches the actual roster
  - determinism: identical seed + policy replays to an identical world hash
  - no exception escapes: every failure is recorded, never raised past a seed
  - job lifecycle: decisions/cryo run through server.job; every job must reach
    a terminal status and release the single BUSY slot (watchdog exercised)

Usage:
    .venv/bin/python stress/run_battery.py --seeds 12 --years 60 --scale 1024
    (plain python3 works if PyYAML is importable)

Results: one JSON line per seed (default stress/results/battery_<stamp>.jsonl)
plus a summary line on stdout. Exit code 1 if any seed failed an assertion.
Never touches the repository's state/ directory: saves use a temp directory.
"""
import argparse
import copy
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE))

import offline_mocks  # noqa: E402  (stress/ sibling)

offline_mocks.install()

import engine  # noqa: E402
# engine prepends sim_engine/ for its imports; pin the repository root back
# in front before resolving this project's server module.
sys.path.insert(0, str(BRIDGE))
import server  # noqa: E402
import voyage  # noqa: E402
import outbreak  # noqa: E402
import live_voyage  # noqa: E402

# === Policy ladder: first plan that isn't blocked wins; hold is the floor ===
POLICY = {
    "outbreak": [["isolate", "surge_care"], ["isolate"], ["surge_care"], ["hold"]],
    "health": [["medical"], ["hold"]],
    "food": [["gardens"], ["ration"], ["hold"]],
    "repair": [["maintenance"], ["hold"]],
    "trust": [["charter"], ["hold"]],
    "review": [["hold"]],
}
MAX_DECISIONS_PER_INCIDENT = 60
JOB_TIMEOUT = 120


def check(record, condition, label):
    if not condition:
        record["assertion_failures"].append(label)


def world_hash(c):
    return hashlib.sha256(json.dumps(c.world.state, sort_keys=True, default=str).encode()).hexdigest()


def conservation(c, record, initial):
    pop = c.world.state["population"]
    flags = sum(bool(p["alive"]) for p in pop["people"].values())
    check(record, pop["alive"] == flags, f"alive counter {pop['alive']} != flag count {flags}")
    expected = initial + pop["births_total"] - pop["deaths_total"]
    check(record, flags == expected, f"population {flags} != initial {initial} + births {pop['births_total']} - deaths {pop['deaths_total']}")
    by_gen = sum((pop.get("by_generation") or {}).values())
    check(record, by_gen == flags, f"by_generation total {by_gen} != alive {flags}")
    resources = c.world.state["resources"]
    check(record, resources["food_kg"] >= 0, f"negative food {resources['food_kg']}")
    check(record, resources["medicine_kg"] >= 0, f"negative medicine {resources['medicine_kg']}")
    s = outbreak.summary(c)
    if s:
        roster_dead = sum(1 for p in pop["people"].values() if not p["alive"] and p.get("cause_of_death") == "bridge_outbreak")
        check(record, s["deaths"] == roster_dead, f"outbreak deaths {s['deaths']} != roster casualties {roster_dead}")
        accounted = s["infected"] + s["recovered"] + s["deaths"]
        check(record, accounted <= s["ever_infected"] + s["deaths"], "outbreak case ledger exceeds ever_infected")


def run_job(kind, data, record):
    """Push one operation through the real job machinery and wait for terminal."""
    jid = server.job(kind, data)
    record["jobs"] += 1
    deadline = time.monotonic() + JOB_TIMEOUT
    while time.monotonic() < deadline:
        server.watchdog_scan()
        entry = server.JOBS.get(jid, {})
        if entry.get("status") != "running":
            break
        time.sleep(.005)
    entry = server.JOBS.get(jid, {})
    check(record, entry.get("status") in ("done", "failed"), f"job {kind} never terminal: {entry.get('status')}")
    if server.BUSY.acquire(blocking=False):
        server.BUSY.release()
    else:
        record["assertion_failures"].append(f"BUSY leaked after {kind} job")
    return entry


def play(c, years, record, use_jobs=True):
    """Full loop until arrival/loss, the year cap, or a policy dead end."""
    initial = c.summary()["crew"]
    server.CAMPAIGNS[c.id] = c
    incident_decisions = 0
    while c.status == "in_flight" and c.world.tick < years:
        p = voyage.ensure(c)
        if p["mode"] == "ready":
            incident_decisions = 0
            if use_jobs:
                entry = run_job("cryo", {"campaign_id": c.id, "revision": p["revision"]}, record)
                check(record, entry.get("status") == "done", f"cryo job failed: {entry.get('error')}")
                if entry.get("status") != "done":
                    break
            else:
                live_voyage.cryo(c, lambda *_: None)
        elif p["mode"] == "awake":
            kind = (p.get("incident") or {}).get("kind")
            incident_decisions += 1
            if incident_decisions > MAX_DECISIONS_PER_INCIDENT:
                record["assertion_failures"].append(f"incident {kind} unresolved after {MAX_DECISIONS_PER_INCIDENT} decisions")
                break
            record["wakes"].setdefault(kind or "unknown", 0)
            committed = False
            for ops in POLICY.get(kind, [["hold"]]):
                decision = "Battery order OPS:" + "+".join(ops)
                if use_jobs:
                    entry = run_job("decision_text", {"campaign_id": c.id, "revision": p["revision"], "decision": decision}, record)
                    if entry.get("status") != "done":
                        record["assertion_failures"].append(f"decision job failed: {entry.get('error')}")
                        break
                    result = entry.get("result") or {}
                else:
                    try:
                        result = live_voyage.decide(c, decision, lambda *_: None)
                    except Exception as exc:
                        record["assertion_failures"].append(f"decide raised {type(exc).__name__}: {exc}")
                        break
                if result.get("blocked"):
                    record["blocked"] += 1
                    continue
                committed = True
                record["decisions"] += 1
                record["wakes"][kind or "unknown"] += 1
                break
            if not committed:
                record["assertion_failures"].append(f"no viable policy for incident {kind}")
                break
        else:  # ended
            break
        conservation(c, record, initial)
    record["years_simulated"] = c.world.tick
    record["final_status"] = c.status
    s = c.summary()
    record["final_crew"], record["births"], record["deaths"] = s["crew"], s["births"], s["deaths"]
    epidemic = outbreak.summary(c)
    if epidemic:
        record["outbreak"] = {k: epidemic[k] for k in ("status", "day", "infected", "recovered", "deaths", "ever_infected")}
    return record


def build_campaign(seed, scale):
    ship = "ship_modular_cluster" if scale < 500 else "ship_stanford_torus" if scale < 1000 else "ship_aurora_ark"
    options = {"crew": scale, "ship": ship, "drive": "prop_fusion_continuous", "speed": .05, "launch_year": 2250, "reserve_days": 300, "medicine_years": 8, "ethos": "mixed_voluntary", "scenario": "outbreak"}
    cfg = engine.make_config(options)
    cfg["tunables"]["rng_seed"] = seed  # deterministic instead of secrets
    return cfg


def run_seed(seed, years, scale, probe_determinism):
    record = {"seed": seed, "scale": scale, "assertion_failures": [], "decisions": 0, "blocked": 0, "jobs": 0, "wakes": {}, "determinism_ok": None}
    started = time.monotonic()
    try:
        cfg = build_campaign(seed, scale)
        c = engine.Campaign(copy.deepcopy(cfg))
        play(c, years, record, use_jobs=True)
        if probe_determinism:
            probe_years = min(years, 12)
            hashes = []
            for _ in range(2):
                probe = {"seed": seed, "scale": scale, "assertion_failures": [], "decisions": 0, "blocked": 0, "jobs": 0, "wakes": {}}
                pc = engine.Campaign(copy.deepcopy(cfg))
                play(pc, probe_years, probe, use_jobs=False)
                hashes.append(world_hash(pc))
                record["assertion_failures"].extend("probe: " + f for f in probe["assertion_failures"])
            record["determinism_ok"] = hashes[0] == hashes[1]
            check(record, record["determinism_ok"], "same seed + policy produced diverging world state")
    except Exception as exc:  # no exception may escape a seed
        record["assertion_failures"].append(f"ESCAPED {type(exc).__name__}: {exc}")
    record["wall_seconds"] = round(time.monotonic() - started, 2)
    record["ok"] = not record["assertion_failures"]
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=10, help="number of seeds (campaigns) to run")
    parser.add_argument("--years", type=int, default=60, help="cap on simulated voyage years per seed")
    parser.add_argument("--scale", type=int, default=1024, help="founding population per campaign")
    parser.add_argument("--seed-base", type=int, default=90000, help="first seed value")
    parser.add_argument("--determinism-probes", type=int, default=2, help="seeds that get a double-run determinism check")
    parser.add_argument("--out", default=None, help="JSONL output path")
    args = parser.parse_args()

    # Redirect all saves away from the repository's state/ before anything runs.
    temp = tempfile.mkdtemp(prefix="proxima-battery-")
    engine.ROOT = server.ROOT = Path(temp)
    (Path(temp) / "state").mkdir()

    out = Path(args.out) if args.out else Path(__file__).parent / "results" / f"battery_{time.strftime('%Y-%m-%d_%H%M%S')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with out.open("w") as sink:
        for i in range(args.seeds):
            record = run_seed(args.seed_base + i, args.years, args.scale, probe_determinism=i < args.determinism_probes)
            sink.write(json.dumps(record) + "\n")
            sink.flush()
            failures += 0 if record["ok"] else 1
            marker = "ok " if record["ok"] else "FAIL"
            print(f"[{marker}] seed {record['seed']}: {record.get('years_simulated', '?')}y, {record['decisions']} decisions, {record['jobs']} jobs, crew {record.get('final_crew', '?')}, {record['wall_seconds']}s" + ("" if record["ok"] else f" — {record['assertion_failures'][:3]}"), flush=True)
    print(f"\nBattery complete: {args.seeds} seeds, {failures} with failures. Results: {out}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
