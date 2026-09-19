"""Events + EventBus + Effect.

Events are the only legal way one system affects another. Effects are explicit
state mutations applied by the bus (not handlers), so every state change is
traceable to a specific event with provenance.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Literal


EffectOp = Literal["set", "add", "mul", "clamp", "set_if_higher", "set_if_lower"]


@dataclasses.dataclass(frozen=True)
class Effect:
    path: str                  # dotted path into world.state, e.g. 'mortality.modifiers.outbreak'
    op: EffectOp
    value: Any
    condition: str | None = None  # optional predicate (evaluated against world)


@dataclasses.dataclass(frozen=True)
class Event:
    kind: str                  # short slug
    source: str                # name of emitting system
    tick: int
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    effects: tuple[Effect, ...] = ()
    anchored_in: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "tick": self.tick,
            "payload": self.payload,
            "effects": [dataclasses.asdict(e) for e in self.effects],
            **({"anchored_in": self.anchored_in} if self.anchored_in else {}),
        }


class EventBus:
    """Per-tick event queue + dispatcher.

    emit() queues an event for end-of-tick dispatch.
    dispatch_pending() runs in the orchestrator after all systems tick — it
    applies effects to state and calls subscribers. Effects are applied first,
    then subscriber callbacks (so callbacks see the post-effect state).
    """

    # Why bounded: nothing in sim/ or bridge/ reads the full archive (verified
    # 2026-09-18 — archive() had zero callers), but on a 50k-crew, 1,000+ year
    # run the birth/death event stream alone is hundreds of MB. We keep a total
    # count plus a recent tail for debugging instead of every event forever.
    ARCHIVE_TAIL = 1024

    __slots__ = ("_pending", "_archive", "_archived_total", "_subscribers")

    def __init__(self) -> None:
        self._pending: list[Event] = []
        self._archive: list[Event] = []
        self._archived_total: int = 0
        self._subscribers: dict[str, list[Callable[[Any, Event], None]]] = {}

    def emit(self, event: Event) -> None:
        self._pending.append(event)

    def subscribe(self, kind: str, handler: Callable[[Any, Event], None]) -> None:
        self._subscribers.setdefault(kind, []).append(handler)

    def dispatch_pending(self, world: Any) -> list[Event]:
        """Apply effects + run subscribers. Returns the dispatched events for trajectory log."""
        dispatched: list[Event] = []
        # process events in emission order
        while self._pending:
            event = self._pending.pop(0)
            for effect in event.effects:
                _apply_effect(world, effect)
            for handler in self._subscribers.get(event.kind, []):
                handler(world, event)
            dispatched.append(event)
            self._archive.append(event)
            self._archived_total += 1
        if len(self._archive) > self.ARCHIVE_TAIL:
            del self._archive[: len(self._archive) - self.ARCHIVE_TAIL]
        return dispatched

    def archive(self) -> list[Event]:
        """Recent tail only (last ARCHIVE_TAIL events). Full stream lives in the trajectory."""
        return list(self._archive)

    def archived_total(self) -> int:
        return self._archived_total


def _apply_effect(world: Any, effect: Effect) -> None:
    """Mutate world.state per the effect. Path is dotted: 'system.field' or 'system.field.subfield'."""
    parts = effect.path.split(".")
    if len(parts) < 2:
        raise ValueError(f"effect path must be at least 'system.field': {effect.path}")
    system_name = parts[0]
    if system_name not in world.state:
        raise KeyError(f"effect path references unknown system: {system_name}")
    # Walk into the slice
    cur = world.state[system_name]
    for p in parts[1:-1]:
        if isinstance(cur, dict):
            if p not in cur:
                cur[p] = {}
            cur = cur[p]
        elif isinstance(cur, list):
            cur = cur[int(p)]
        else:
            raise TypeError(f"cannot traverse {type(cur)} at part {p} in path {effect.path}")
    last = parts[-1]
    op = effect.op
    val = effect.value
    if op == "set":
        cur[last] = val
    elif op == "add":
        prev = cur.get(last, 0) if isinstance(cur, dict) else 0
        cur[last] = (prev if prev is not None else 0) + val
    elif op == "mul":
        prev = cur.get(last, 1) if isinstance(cur, dict) else 1
        if prev is None:
            # Skip mul on None — the field is intentionally absent (e.g., ai_legitimacy on non-AI regime)
            return
        cur[last] = prev * val
    elif op == "clamp":
        # value should be (lo, hi)
        lo, hi = val
        v = cur.get(last, 0) if isinstance(cur, dict) else 0
        cur[last] = max(lo, min(hi, v))
    elif op == "set_if_higher":
        v = cur.get(last, float("-inf")) if isinstance(cur, dict) else float("-inf")
        if val > v:
            cur[last] = val
    elif op == "set_if_lower":
        v = cur.get(last, float("inf")) if isinstance(cur, dict) else float("inf")
        if val < v:
            cur[last] = val
    else:
        raise ValueError(f"unknown effect op: {op}")
