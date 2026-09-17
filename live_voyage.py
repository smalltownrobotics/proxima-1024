"""V2 live loop. Jev paints real batch results; Astra interprets bounded orders.

Physical constraints remain code-owned. The captain's cryo advances annual engine
ticks and uses Jev as a semantic watch alongside deterministic emergency checks.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
import math
import time

from providers import credential, request_json, function, parse_answer, MODEL, JEV_MODEL
from voyage import OPERATIONS, INCIDENTS, ensure, barriers, available_operations, validate_plan, commit, urgent, open_incident, travel_tick
import outbreak


def _council_context(row):
    """Keep advice attributable without resending every consulted profile.

    Sampling and synthetic-voice provenance are load-bearing: dropping either
    lets later Astra turns mistake a sampled council for the whole ship, or a
    fictional voiced perspective for independently recorded expert testimony.
    """
    keys = ("id", "group", "group_label", "person_id", "question", "revision", "year", "people", "eligible_total", "sampled", "sampling_note", "voice_label", "distribution_note", "read_only", "mean_distribution", "labels", "synthesis")
    compact = {key: copy.deepcopy(row[key]) for key in keys if key in row}
    members = {person["id"]: person for person in row.get("members", [])}
    positions = row.get("positions", {})
    experts = []
    for expert in row.get("experts", []):
        identity = members.get(expert["person_id"], {})
        voice = {key: copy.deepcopy(expert[key]) for key in ("person_id", "statement") if key in expert}
        voice.update({key: copy.deepcopy(identity[key]) for key in ("name", "specialty", "experience_years", "synthetic_profile", "profile_source") if key in identity})
        if expert["person_id"] in positions:
            voice["position"] = copy.deepcopy(positions[expert["person_id"]])
        experts.append(voice)
    compact["experts"] = experts
    return compact


def context(c):
    p = ensure(c)
    # A repeat cross-ship poll or specialist follow-up must not erase the medical
    # council's distinct evidence. Keep the latest of each group/person scope
    # within the six retained records, then restore actual chronology.
    latest = {}
    for index, row in enumerate(p["consultations"][-6:]):
        scope = (row.get("group"), str(row["person_id"]) if row.get("person_id") is not None else None)
        latest[scope] = (index, row)
    councils = [_council_context(row) for _, row in sorted(latest.values(), key=lambda item: item[0])]
    return {"ship": c.config["name"], "mission": c.config["mission"], "stats": c.summary(), "revision": p["revision"], "incident": p["incident"], "outbreak": outbreak.summary(c), "jev_crisis_assessment": p.get("crisis_assessment"), "councils": councils, "captain": {"role": "admiral", "state": "cryogenesis" if p["mode"] == "cryo" else "awake", "cycles": p["captain_cycles"], "awake_days": p["captain_awake_days"], "slept_years": p["captain_slept_years"]}, "barriers": barriers(c), "recent_orders": [{"decision": h.get("decision", h["title"]), "year": h["year"], "after": h["after"], "outbreak_result": h.get("outbreak_result")} for h in c.history[-4:]], "synthetic_game": True}


def refresh_crisis_assessment(c, trigger):
    """Assess an actual saved wake/commit, never a proposed order or forecast.

    A failed assessment cannot undo the physical event. Replace stale readings
    with an explicit unavailable marker and preserve the detailed audit privately.
    The stamp also prevents a second provider call for unchanged state, including
    repeated reads after failure; a subsequent actual revision is the next retry.
    """
    from advisory import assess
    p = ensure(c)
    if (p.get("incident") or {}).get("kind") != "outbreak":
        # These axes compare spread, care and stores. A reactor fault is not a
        # disease event; ordinary incidents retain Astra's grounded briefing.
        if p.pop("crisis_assessment", None) is not None:
            c.save()
        return None
    stamp = {"assessment_version": 1, "revision": p["revision"], "year": c.world.tick, "day": (outbreak.summary(c) or {}).get("day")}
    previous = p.get("crisis_assessment") or {}
    if all(previous.get(key) == value for key, value in stamp.items()):
        return previous
    audit = {"purpose": "current_state_crisis_assessment", "trigger": trigger, **stamp, "timestamp": time.time()}
    try:
        report = assess(c)
        if not isinstance(report, dict) or not isinstance(report.get("summary"), dict) or not isinstance(report.get("audit"), dict):
            raise RuntimeError("Jev did not return a complete current-state assessment.")
        p["crisis_assessment"] = {**report["summary"], **stamp, "read_only": True}
        audit.update(status="complete", provider=copy.deepcopy(report["audit"]))
    except Exception as exc:
        p["crisis_assessment"] = {**stamp, "read_only": True, "unavailable": "Jev's current-state assessment is unavailable. The actual ship state is preserved."}
        audit.update(status="unavailable", error=str(exc), error_type=type(exc).__name__)
    p["advisory_audits"].append(audit)
    c.save()
    return p["crisis_assessment"]


def astra(c, message, purpose="chat"):
    """A short, real model voice. No prose from a fallback is labeled Astra."""
    payload = {"model": MODEL, "store": False, "reasoning": {"effort": "low"}, "max_output_tokens": 360, "instructions": "You are Astra aboard a fictional generation ship. Answer in at most TWO short sentences, 45 words total. Plain language, vivid but exact. Use only supplied state; no invented incidents, casualties or actions. Explain what matters now, not all metrics. Do not repeat disclaimers; the interface already labels this a simulation. No bullet lists. During chat, never imply you executed an order. Cryo is the captain's time-skip; the crew remains active. The ending is unwritten. A resolved flag means the stated incident criterion was met, not every ship problem cured.", "input": [{"role": "user", "content": json.dumps(context(c))}, *[{"role": m["role"], "content": m["text"]} for m in c.messages[-4:]], {"role": "user", "content": message}]}
    if purpose == "chat":
        payload.update(max_output_tokens=3000, reasoning={"effort": "high"})
        payload["instructions"] = "You are Astra, the intelligence partner to an admiral in cryogenesis on a fictional generation ship. The admiral alone authorizes actions. Your job is to make a novel, difficult crisis understandable enough for their final decision. This is a READ-ONLY planning conversation: no tools or authority to execute orders, spend resources, advance time, or convene a council implicitly. Ground facts in the supplied state. Distinguish measured engine state, Jev's model-estimated probabilities, uncertainty, and hypothetical alternatives. Use up to 170 words with two or three short paragraphs, not a wall of metrics. Explain a consequential tradeoff and one useful next question or specialist group to consult. Discuss the supported fictional options without clinical or engineering certainty. Never invent casualties, expert testimony, consensus, or numerical counterfactual outcomes. Jev consultation probabilities are recommendations, not votes or guarantees; stale consultations with a different revision describe an earlier state. The captain can convene the medical, engineering, growers, civic or whole-crew council via the consultation screen. Do not say you consulted anyone unless a supplied council record exists. You can help draft an order but clearly call it a draft. Prefer direct, thoughtful language over generic reassurance. The ship is on a multi-generation voyage; the player is the admiral awakened only for consequential decisions."
    else:
        payload["instructions"] += " Address the player as Admiral. A plague may kill people despite a supported intervention; report only actual supplied cases and deaths. Jev probabilities affect cooperation and social response, while the engine computes physical outcomes."
    reply = request_json("https://api.openai.com/v1/responses", credential("OPENAI_API_KEY"), payload)
    text = "\n".join(x["text"] for item in reply.get("output", []) if item.get("type") == "message" for x in item.get("content", []) if x.get("type") == "output_text").strip()
    if not text:
        raise RuntimeError("Astra's narration was incomplete. The interface shows the saved ship state; no order needs to be repeated.")
    c.astra_runs.append({"model": reply.get("model"), "id": reply.get("id"), "purpose": purpose, "usage": reply.get("usage"), "output": reply.get("output"), "timestamp": time.time()})
    c.messages += [{"role": "user", "text": message}, {"role": "assistant", "text": text}]
    ensure(c)["voice"] = text
    c.save()
    return text


def compile_order(c, decision):
    supported = available_operations(c)
    definition = function("interpret_order", "Interpret the captain's text as existing ship operations. Flag unsupported capabilities instead of inventing them.", {"operations": {"type": "array", "items": {"type": "string", "enum": list(supported)}, "maxItems": 3}, "interpretation": {"type": "string"}, "unsupported": {"type": "boolean"}, "reason": {"type": "string"}}, ["operations", "interpretation", "unsupported", "reason"])
    payload = {"model": MODEL, "store": False, "reasoning": {"effort": "low"}, "max_output_tokens": 650, "parallel_tool_calls": False, "tools": [definition], "tool_choice": {"type": "function", "name": "interpret_order"}, "instructions": "Map the player's decision to one to three supported operations WITHOUT silently changing their intent. Do not execute anything. If the decision relies on unsupported technology, fabricated supplies, harming or killing people, impossible travel, or actions absent from the allowed operation list, set unsupported=true and explain the barrier in one sentence. Questions are not orders: flag ambiguous intent and request an actionable decision. An advisory vote is charter; medicine/fabrication for care is medical; fair labor alone can modify the tone of another order but creates no extra mechanics. Choosing to accept the risk is hold, only when explicit. Return interpretation in at most 14 words. Do not invent numbers or state paths.", "input": json.dumps({"captain_decision": decision, "ship_state": context(c), "supported_operations": OPERATIONS})}
    payload["input"] = json.dumps({"captain_decision": decision, "ship_state": context(c), "supported_operations": supported})
    payload["instructions"] += " During an active outbreak, isolating affected decks/quarantining is isolate; opening emergency wards/diverting reserves to treatment is surge_care; explicitly keeping normal food production running instead of isolation is protect_food. Choose only operations actually requested. Do not silently add protect_food merely because a captain wants the crew to survive. Life-or-death consequences of care policies are simulated by the engine; do not reject an otherwise supported treatment policy simply because deaths are possible. Waiting is hold and advances the disease; it does not end the emergency. Broad intentions like save lives add no unsupported mechanic."
    reply = request_json("https://api.openai.com/v1/responses", credential("OPENAI_API_KEY"), payload)
    calls = [x for x in reply.get("output", []) if x.get("type") == "function_call" and x.get("name") == "interpret_order"]
    if len(calls) != 1:
        raise RuntimeError("Astra could not turn that into an executable order. Nothing changed.")
    plan = json.loads(calls[0]["arguments"])
    if not isinstance(plan, dict):
        raise RuntimeError("Astra returned an invalid operation plan. Nothing changed.")
    audit = {"model": reply.get("model"), "id": reply.get("id"), "purpose": "order_compiler_v2", "usage": reply.get("usage"), "timestamp": time.time(), "decision": decision, "plan": plan}
    return plan, audit


def evaluate_people(c, decision, pulse):
    people = c.crew()
    state = context(c)
    batches = [people[i:i + 50] for i in range(0, len(people), 50)]
    start = time.monotonic()
    key = credential("TYPESAFE_API_KEY")
    def batch_run(batch):
        questions = {"crew_" + p["id"]: {"type": "choice", "instructions": f"Estimate the response of fictional crew member {p['id']} to this specific captain decision. Use their priorities, temperament and current situation. Support can coexist with recognizing a cost. Question only when meaningful uncertainty, unfairness or missing detail would prevent cooperation. This is a game, not an estimate about a real person.", "criteria": {"support": "Would cooperate with this order given its concrete benefits and costs.", "question": "Needs material clarification or negotiation before cooperating.", "oppose": "Would resist because the order materially conflicts with their priorities."}} for p in batch}
        payload = {"model": JEV_MODEL, "state": {"ship": state, "captain_decision": decision, "crew": batch, "supported_mechanics": available_operations(c)}, "questions": questions}
        reply = request_json("https://api.typesafe.ai/v1/systemone", key, payload)
        results = {}
        for person in batch:
            probs, confidence = parse_answer(reply.get("answers", {}).get("crew_" + person["id"], {}))
            # America1024's visual semantics: color is the top class; the HUD
            # and actual social effects still use every probability, not votes.
            results[person["id"]] = {"probabilities": probs, "confidence": confidence, "choice": max(probs, key=probs.get)}
        return results, {"request": payload, "response": reply, "timestamp": time.time(), "requested_model": JEV_MODEL, "returned_model": reply.get("model")}
    reactions, audits = {}, []
    pulse({"phase": "Jev is reading your decision", "jev": {"received": 0, "total": len(people), "responses": {}, "seconds": 0, "mean_distribution": {"support": 0, "question": 0, "oppose": 0}}})
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(batch_run, batch) for batch in batches]
        for future in as_completed(futures):
            responses, audit = future.result()
            reactions.update(responses)
            audits.append(audit)
            means = {k: sum(r["probabilities"][k] for r in reactions.values()) / len(reactions) for k in ("support", "question", "oppose")}
            pulse({"phase": "Jev · live crew response", "jev": {"received": len(reactions), "total": len(people), "responses": reactions.copy(), "seconds": round(time.monotonic() - start, 2), "mean_distribution": means}})
    summary = {"people": len(people), "seconds": round(time.monotonic() - start, 2), "model": JEV_MODEL, "year": c.world.tick, "action": decision, "mean_distribution": means}
    return reactions, {"summary": summary, "batches": audits, "prompt_version": "voyage-2", "decision": decision}


def decide(c, decision, pulse):
    p = ensure(c)
    if p["mode"] != "awake":
        raise ValueError("No unresolved incident is waiting for an order.")
    # Jev starts immediately; interpretation does not hide its fast live result.
    with ThreadPoolExecutor(max_workers=2) as pool:
        interpretation = pool.submit(compile_order, c, decision)
        population = pool.submit(evaluate_people, c, decision, pulse)
        reactions, audit = population.result()
        pulse({"phase": "Jev complete · checking the executable plan"})
        plan, astra_audit = interpretation.result()
    c.astra_runs.append(astra_audit)
    if plan.get("unsupported"):
        reason = str(plan.get("reason") or "This decision asks for capabilities the ship does not have.")
        p["last_preview"] = {"decision": decision, "blocked": True, "reason": reason, "jev": audit["summary"]}
        # Preserve genuine output, but don't count an uncommitted preview as an order.
        p.setdefault("blocked_runs", []).append(audit)
        c.save()
        return {"campaign": c.public(), "blocked": True, "reason": reason, "responses": reactions, "jev": audit["summary"]}
    try:
        validate_plan(c, plan)
    except ValueError as exc:
        p["last_preview"] = {"decision": decision, "blocked": True, "reason": str(exc), "jev": audit["summary"]}
        p.setdefault("blocked_runs", []).append(audit)
        c.save()
        return {"campaign": c.public(), "blocked": True, "reason": str(exc), "responses": reactions, "jev": audit["summary"]}
    record = commit(c, decision, plan, reactions, audit)
    frames = record.get("outbreak_frames", [])
    if record.get("outbreak_result"):
        # commit has already persisted real people, resources and the tactical
        # day. Jev now describes that new state, even if the plague is contained.
        refresh_crisis_assessment(c, "outbreak_commit")
    pulse({"phase": "28 days of consequences computed" if frames else "Your decision changed the ship", "campaign": c.public(), "outbreak_frames": frames})
    try:
        compact = {k: v for k, v in record.items() if k != "outbreak_frames"}
        voice = astra(c, "Report the consequence of this exact committed order in two short sentences. For an outbreak name actual deaths, surviving cases, and the resource cost. Do not claim deaths were prevented or lives saved without a measured counterfactual. If the incident is resolved, invite me back into cryo; otherwise state the remaining barrier. Order: " + json.dumps(compact), "consequence")
    except Exception:
        return {"campaign": c.public(), "committed": True, "outbreak_frames": frames, "narration_error": "Your decision is committed and saved. Astra's narration is unavailable; inspect the updated ship or continue."}
    return {"campaign": c.public(), "text": voice, "committed": True, "outbreak_frames": frames}


def watch(c, change):
    state = {"stats": c.summary(), "outbreak": outbreak.summary(c), "change": {k: v for k, v in change.items() if k != "events"}, "recent_events": [e.get("kind") for e in change["events"]][-12:], "last_incident": ensure(c)["incident"], "question": "Is there a material new need for the captain's intervention? This is a fictional game watch, not a safety system."}
    request = {"model": JEV_MODEL, "state": state, "questions": {"watch": {"type": "choice", "instructions": "Choose whether to wake the captain for a material intervention. Ordinary births, aging and small healthy changes are not reasons to wake them. Depleted medicine, inadequate food, failing integrity, serious legitimacy/morale trouble or a newly consequential event can justify waking. Do not invent a problem.", "criteria": {"wake": "Current conditions warrant a deliberate captain decision.", "continue": "Routine ship management is sufficient; let the captain sleep."}}}}
    reply = request_json("https://api.typesafe.ai/v1/systemone", credential("TYPESAFE_API_KEY"), request)
    probs = reply.get("answers", {}).get("watch", {}).get("probabilities", {})
    if set(probs) != {"wake", "continue"} or any(not math.isfinite(float(v)) or float(v) < 0 for v in probs.values()) or sum(probs.values()) <= 0:
        raise RuntimeError("Jev returned an invalid watch distribution. Cryo has paused at the last saved year.")
    total = sum(probs.values())
    value = float(probs["wake"]) / total
    ensure(c)["wake_checks"].append({"year": c.world.tick, "wake_probability": value, "request": request, "response": reply, "timestamp": time.time()})
    return value


def cryo(c, pulse):
    p = ensure(c)
    if p["mode"] != "ready" or c.status != "in_flight":
        raise ValueError("Resolve the current incident, or explicitly accept its risk, before returning to cryo.")
    if (outbreak.summary(c) or {}).get("status") == "active":
        raise ValueError("You cannot leave the bridge while the outbreak is active. Choose the next 28-day response.")
    p["mode"] = "cryo"
    p["captain_cycles"] += 1
    start = c.world.tick
    frames = []
    try:
        for _ in range(min(12, c.summary()["remaining"])):
            change = travel_tick(c)
            kind = urgent(c)
            if kind == "outbreak":
                # Onset is already real. Lock the emergency BEFORE a network
                # call so disconnect/restart cannot strand it in ready/cryo.
                open_incident(c, kind, [{"year": c.world.tick, "stats": c.summary(), "outbreak": outbreak.summary(c)}])
            # Persist every actual engine tick before the network watch request.
            c.save()
            probability = watch(c, change) if c.status == "in_flight" else 0
            elapsed = c.world.tick - start
            should_wake = c.status == "in_flight" and (kind == "outbreak" or kind and elapsed >= 2 or probability >= .72 and elapsed >= 3)
            if should_wake and p["mode"] != "awake":
                open_incident(c, kind or "review", [{"year": c.world.tick, "watch_probability": probability, "stats": c.summary()}])
            frame = {"stats": c.summary(), "crew": c.crew(), "distance_traveled_ly": p["distance_traveled_ly"], "births": change["births"], "deaths": change["deaths"], "wake_probability": probability, "wake": bool(should_wake), "mode": p["mode"]}
            frames.append(frame)
            pulse({"phase": "Captain in cryo · ship time advancing", "frames": frames.copy(), "campaign": c.public()})
            c.save()
            if should_wake or c.status != "in_flight":
                break
        if p["mode"] == "cryo":
            # A bounded watch window ends quietly; it is not a fabricated crisis.
            p["mode"] = "ready"
        if p["mode"] == "awake":
            # A separate Jev lens makes uncertainty/pressure visible before
            # deliberation. Failure cannot erase a real wake or invent an answer.
            refresh_crisis_assessment(c, "wake")
            voice = astra(c, "Wake me with the one thing that requires my decision, and the real tradeoff. Two short sentences.", "wake")
        elif c.status != "in_flight":
            voice = astra(c, "Give the voyage's ending in two short sentences. Arrival means reaching the destination, not proof of a habitable world.", "ending")
        else:
            voice = ""
        c.save()
        return {"campaign": c.public(), "frames": frames, "text": voice}
    except Exception:
        if p["mode"] == "cryo":
            p["mode"] = "ready"
        c.save()
        raise
