"""Offline provider mocks for stress/reliability tooling.

Replaces every live Astra/Jev/advisory call with deterministic local stand-ins
so full campaign loops run with NO network and NO credentials. Never import
this module in the production server process.
"""
import hashlib
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
# engine prepends sim_engine/ for its imports; keep the repository root first
# so this project's server module is resolved consistently.
for entry in (str(BRIDGE),):
    if entry not in sys.path[:1]:
        sys.path.insert(0, entry)

import advisory  # noqa: E402
import live_voyage  # noqa: E402


LABELS = ("support", "question", "oppose")


def _distribution(pid, decision):
    """Deterministic, mildly varied per-person distribution. No RNG state."""
    h = int(hashlib.sha256(f"{pid}:{decision}".encode()).hexdigest()[:8], 16)
    support = .55 + (h % 30) / 100          # .55 – .84
    oppose = .04 + (h >> 8) % 8 / 100       # .04 – .11
    question = 1 - support - oppose
    return {"support": support, "question": question, "oppose": oppose}


def mock_compile_order(c, decision):
    """Interpret 'OPS:a+b' decision text literally; anything else is hold."""
    ops = decision.split("OPS:", 1)[1].split("+") if "OPS:" in decision else ["hold"]
    plan = {"operations": [op.strip() for op in ops if op.strip()], "interpretation": "battery order", "unsupported": False, "reason": ""}
    return plan, {"summary": {"mock": True}, "purpose": "battery_compile", "decision": decision}


def mock_evaluate_people(c, decision, pulse):
    people = c.crew()
    reactions = {}
    for person in people:
        probs = _distribution(person["id"], decision)
        reactions[person["id"]] = {"probabilities": probs, "confidence": .9, "choice": max(probs, key=probs.get)}
    n = max(1, len(reactions))
    means = {k: sum(r["probabilities"][k] for r in reactions.values()) / n for k in LABELS}
    pulse({"phase": "Jev is reading your decision", "jev": {"received": 0, "total": len(people), "responses": {}, "seconds": 0, "mean_distribution": {k: 0 for k in LABELS}}})
    pulse({"phase": "Jev · live crew response", "jev": {"received": len(people), "total": len(people), "responses": {}, "seconds": .01, "mean_distribution": means}})
    summary = {"people": len(people), "seconds": .01, "model": "mock-jev", "year": c.world.tick, "action": decision, "mean_distribution": means}
    return reactions, {"summary": summary, "batches": [], "prompt_version": "battery-mock", "decision": decision}


def mock_watch(c, change):
    """Deterministic pseudo-probability; occasionally crosses the wake bar."""
    value = (c.world.tick * 37 % 100) / 100
    live_voyage.ensure(c)["wake_checks"].append({"year": c.world.tick, "wake_probability": value, "request": {"mock": True}, "response": {"mock": True}, "timestamp": 0})
    return value


def mock_astra(c, message, purpose="chat"):
    text = "Battery narration: state saved, no live model consulted."
    c.messages += [{"role": "user", "text": str(message)[:200]}, {"role": "assistant", "text": text}]
    return text


def mock_assess(c):
    return {"summary": {"urgency": {"label": "elevated", "choice": "elevated", "probabilities": {"elevated": 1.0}, "labels": {"elevated": "Elevated"}}}, "audit": {"mock": True}}


def install():
    """Patch every provider seam in place. Returns nothing; process-wide."""
    live_voyage.compile_order = mock_compile_order
    live_voyage.evaluate_people = mock_evaluate_people
    live_voyage.watch = mock_watch
    live_voyage.astra = mock_astra
    advisory.assess = mock_assess
