"""EmergentEventsSystem — template-firing engine.

Owns:    world.state['events_history']
Reads:   factions, tension, governance, morale, population, resources
Emits:   per-template events (schism, mutiny, sect_radicalization, mass_casualty,
         faction_split, generational_revolt, religious_fusion, succession_violence)

Reads templates from data/event_templates.yaml. Each tick: evaluates each
template's trigger; if it fires, picks a protagonist faction (or pair) using the
selector, then applies effects. Tracks per-template last-fire year to enforce
cooldowns (min_years_since_last).

This is the layer that produces emergent narratives without scripting specific
events. The TYPE of event is from a fixed catalog; the WHEN, WHERE, and WHO
arise from accumulated state.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framework import BaseSystem, Event  # noqa: E402

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_EVENT_TEMPLATES: list[dict] | None = None


def load_event_templates() -> list[dict]:
    global _EVENT_TEMPLATES
    if _EVENT_TEMPLATES is None:
        with open(DATA_DIR / "event_templates.yaml") as f:
            _EVENT_TEMPLATES = yaml.safe_load(f)["events"]
    return _EVENT_TEMPLATES


class EmergentEventsSystem(BaseSystem):
    name = "events_engine"
    dependencies = ["factions", "tension", "governance", "morale", "population"]
    emits = ["schism", "mutiny", "sect_radicalization", "mass_casualty", "faction_split",
             "generational_revolt", "religious_fusion", "governance_revolt"]

    def defaults(self, config: dict) -> dict:
        return {
            "history": [],                          # full event log emitted from this engine
            "last_fire_year": {},                   # template_id -> last fire tick
            "fire_count": {},                       # template_id -> total fires
        }

    def tick(self, world: Any, dt_years: float) -> None:
        slice_ = world.state[self.name]
        for template in load_event_templates():
            tid = template["id"]
            cooldown_years = float(template.get("trigger", {}).get("min_years_since_last", 30))
            last = slice_["last_fire_year"].get(tid, -9999)
            if (world.tick - last) < cooldown_years:
                continue
            fired = self._evaluate_and_fire(world, template)
            if fired:
                slice_["last_fire_year"][tid] = world.tick
                slice_["fire_count"][tid] = slice_["fire_count"].get(tid, 0) + 1

    def _evaluate_and_fire(self, world: Any, template: dict) -> bool:
        """Evaluate template trigger; if conditions met, pick protagonist + apply effects + log."""
        kind = template["kind"]
        trigger = template.get("trigger", {})
        when = trigger.get("when", "false")

        # Pick protagonist using selector
        prot = self._select_protagonist(world, template)
        if prot is None:
            return False

        # Evaluate trigger expression substituting protagonist
        if not self._eval_trigger(world, when, prot):
            return False

        # Apply effects (best-effort — skip ones that don't make sense)
        applied = []
        for effect in template.get("effects", []):
            try:
                self._apply_effect(world, effect, prot)
                applied.append(effect)
            except Exception as e:
                pass

        # Log + emit
        event_payload = {
            "kind": kind,
            "template": template["id"],
            "protagonist": prot,
            "effects_applied": len(applied),
            "anchored_in": template.get("anchored_in", ""),
        }
        world.state[self.name]["history"].append({"tick": world.tick, **event_payload})
        world.events.emit(Event(
            kind=kind, source=self.name, tick=world.tick,
            payload=event_payload,
            anchored_in=template.get("anchored_in"),
        ))
        return True

    def _select_protagonist(self, world: Any, template: dict) -> dict | None:
        """Return {f: <faction_id>} or {a, b: <ids>} based on selector kind."""
        sel = template.get("protagonist_selector", {}) or {}
        kind = sel.get("kind", "faction_with_max")
        metric = sel.get("metric", "alienation")
        filt = sel.get("filter", "")
        factions = world.state.get("factions", {}).get("factions", {})
        if not factions:
            return None
        if kind == "faction_with_max":
            cands = [(fid, f) for fid, f in factions.items() if self._eval_filter(filt, f)]
            if not cands:
                return None
            chosen_id, chosen_f = max(cands, key=lambda x: x[1].get(metric, 0))
            return {"f": chosen_id, "_faction": chosen_f}
        if kind == "faction_with_min":
            cands = [(fid, f) for fid, f in factions.items() if self._eval_filter(filt, f)]
            if not cands:
                return None
            chosen_id, chosen_f = min(cands, key=lambda x: x[1].get(metric, 0))
            return {"f": chosen_id, "_faction": chosen_f}
        if kind == "pair_with_max_tension":
            tension = world.state.get("tension", {})
            ids = tension.get("max_pair_ids", [None, None])
            if ids[0] is None or ids[1] is None:
                return None
            return {"a": ids[0], "b": ids[1], "_tension": tension.get("max_pair", 0)}
        return None

    def _eval_filter(self, filt: str, faction: dict) -> bool:
        """Simple filter evaluator. Supports: 'metric > X', 'metric < X', 'religious_alignment != null'."""
        if not filt:
            return True
        # Very permissive — accept anything reasonable; default to True if can't parse
        try:
            f = faction
            # rewrite 'religious_alignment != null' to 'is not None'
            expr = filt.replace("!= null", "is not None").replace("== null", "is None")
            # rewrite metric names
            for key in ("intensity", "alienation", "share", "class_tier"):
                expr = expr.replace(key, f"f.get('{key}', 0)")
            # rewrite 'religious_alignment'
            expr = expr.replace("religious_alignment", "f.get('religious_alignment')")
            return bool(eval(expr, {"f": f, "__builtins__": {}}))
        except Exception:
            return True   # permissive default

    def _eval_trigger(self, world: Any, when: str, prot: dict) -> bool:
        """Evaluate trigger expression. Permissive: missing values default to 0/false."""
        try:
            # Simplify: rewrite path lookups to dict access
            ctx = self._build_eval_context(world, prot)
            # Naive substitutions for common patterns
            expr = when
            # Replace '<f>' with the chosen faction id (or skip)
            if "<f>" in expr:
                fid = prot.get("f")
                if fid is None:
                    return False
                expr = expr.replace("<f>", fid)
            # Build python expression: 'factions.X.intensity > 0.65' -> ctx['factions']['X']['intensity'] > 0.65
            # Use a dotted-path resolver
            return self._eval_dotted_expr(expr, ctx)
        except Exception:
            return False

    def _build_eval_context(self, world: Any, prot: dict) -> dict:
        """Build a flat dict context with key state values."""
        morale = world.read("morale.aggregate") or 0
        gov = world.state.get("governance", {})
        pop = world.state.get("population", {})
        factions = world.state.get("factions", {}).get("factions", {})
        tension = world.state.get("tension", {})
        return {
            "morale_aggregate": morale,
            "governance_legitimacy": gov.get("legitimacy", 1.0),
            "governance_pressure": gov.get("pressure", 0.0),
            "governance_type": gov.get("type", "council"),
            "governance_years_in_power": gov.get("years_in_power", 0),
            "population_alive": pop.get("alive", 1) if isinstance(pop, dict) else 1,
            "population_deaths_year": pop.get("deaths_year", 0) if isinstance(pop, dict) else 0,
            "population_generations_count": (
                len(pop.get("by_generation", {})) if isinstance(pop, dict) and isinstance(pop.get("by_generation"), dict) else 0
            ),
            "tension_max_pair": tension.get("max_pair", 0),
            "factions": factions,
            "factions_ship_born_share": factions.get("ship_born", {}).get("share", 0),
            "factions_ship_born_alienation": factions.get("ship_born", {}).get("alienation", 0),
            "religion_faction_share": {fid: f.get("share", 0) for fid, f in factions.items()},
            "_prot_faction": prot.get("_faction") or {},
        }

    def _eval_dotted_expr(self, expr: str, ctx: dict) -> bool:
        """Evaluate a dotted-path boolean expression like:
           'factions.<id>.intensity > 0.65 AND governance.legitimacy < 0.45'
        Substitute paths against ctx, then eval as Python."""
        # Normalize AND/OR
        expr = expr.replace(" AND ", " and ").replace(" OR ", " or ")
        # Replace dotted paths with ctx lookups: morale.aggregate -> ctx['morale_aggregate'] etc.
        # We do a small fixed list of substitutions
        substitutions = {
            "morale.aggregate": str(ctx["morale_aggregate"]),
            "governance.legitimacy": str(ctx["governance_legitimacy"]),
            "governance.pressure": str(ctx["governance_pressure"]),
            "governance.type": f"'{ctx['governance_type']}'",
            "governance.years_in_power": str(ctx["governance_years_in_power"]),
            "population.alive": str(ctx["population_alive"]),
            "population.deaths_year": str(ctx["population_deaths_year"]),
            "population.generations_count": str(ctx["population_generations_count"]),
            "tension.max_pair": str(ctx["tension_max_pair"]),
            "factions.ship_born.share": str(ctx["factions_ship_born_share"]),
            "factions.ship_born.alienation": str(ctx["factions_ship_born_alienation"]),
        }
        for k, v in substitutions.items():
            expr = expr.replace(k, v)
        # Replace 'factions.<id>.X' patterns where <id> is the protagonist's faction
        # (handled via earlier substitution of <f> if present)
        # If any 'factions.X.Y' remains, look it up
        import re
        for match in re.finditer(r"factions\.([a-z0-9_]+)\.([a-z_]+)", expr):
            full = match.group(0)
            fid, key = match.group(1), match.group(2)
            val = ctx["factions"].get(fid, {}).get(key, 0)
            expr = expr.replace(full, str(val))
        # 'religion.faction_share[<id>]' -> direct lookup; we don't have religion per-faction yet,
        # so use faction.share as a stand-in
        for match in re.finditer(r"religion\.faction_share\[([a-z0-9_]+)\]", expr):
            full = match.group(0)
            fid = match.group(1)
            val = ctx["factions"].get(fid, {}).get("share", 0)
            expr = expr.replace(full, str(val))
        try:
            return bool(eval(expr, {"__builtins__": {}}, {}))
        except Exception:
            return False

    def _apply_effect(self, world: Any, effect: dict, prot: dict) -> None:
        """Apply a single effect. Substitutes <f>, <a>, <b> with protagonist ids."""
        path = effect["path"]
        op = effect["op"]
        val = effect["value"]
        # Substitute placeholders
        if "<f>" in path and "f" in prot:
            path = path.replace("<f>", prot["f"])
        if "<a>" in path and "a" in prot:
            path = path.replace("<a>", prot["a"])
        if "<b>" in path and "b" in prot:
            path = path.replace("<b>", prot["b"])
        # Handle <new_sect>, <merged_id>, <f_split> by inventing names
        if "<new_sect>" in path:
            path = path.replace("<new_sect>", f"{prot.get('f','x')}_dissident_{world.tick}")
        if "<merged_id>" in path:
            path = path.replace("<merged_id>", f"merged_{prot.get('a','a')}_{prot.get('b','b')}_{world.tick}")
        if "<f_split>" in path:
            path = path.replace("<f_split>", f"{prot.get('f','x')}_split_{world.tick}")
        # Skip if path still has unresolved placeholders or angle-bracket interpolations
        if "<" in path:
            return
        # Skip <all_others> style — would require iteration; leave as future enhancement
        # For value substitutions like "<faction.share * 0.3>" — try to evaluate
        if isinstance(val, str) and val.startswith("<") and val.endswith(">"):
            try:
                expr = val[1:-1]
                # Substitute simple references
                if prot.get("_faction"):
                    expr = expr.replace("faction.share", str(prot["_faction"].get("share", 0)))
                expr = expr.replace("population.alive", str(world.read("population.alive") or 1))
                # No safe eval for arbitrary expressions; just bail to a small constant
                val = float(eval(expr, {"__builtins__": {}}, {})) if any(c.isdigit() for c in expr) else 0.0
            except Exception:
                return
        # Apply via the framework's effect mechanism
        from framework.events import _apply_effect, Effect as Eff
        try:
            _apply_effect(world, Eff(path=path, op=op, value=val))
        except Exception:
            pass

    def emit_snapshot(self, world: Any) -> dict:
        slice_ = world.state[self.name]
        return {
            "fire_count": dict(slice_["fire_count"]),
            "history_count": len(slice_["history"]),
            "recent": slice_["history"][-5:] if slice_["history"] else [],
        }
