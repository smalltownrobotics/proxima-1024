"""System protocol — the contract every behavior implements.

Systems own a slice of world state, advance it each tick, and optionally emit
events. They communicate by writing to their own slice and listening for events
emitted by other systems.

Concrete systems should subclass `System` and override the methods they need.
The `name` attribute is the slice key in `world.state`.
"""
from __future__ import annotations

from typing import Any, Protocol


class System(Protocol):
    """Contract for a sim system."""

    name: str
    dependencies: list[str]
    emits: list[str]
    subscribes: list[str]

    def defaults(self, config: dict) -> dict:
        """Return the initial state slice for this system given a resolved config."""
        ...

    def tick(self, world: Any, dt_years: float) -> None:
        """Advance this system's state by `dt_years`. May emit events via world.events."""
        ...

    def emit_snapshot(self, world: Any) -> dict:
        """Return a JSON-serializable view of this system's current state for the trajectory."""
        ...

    def on_event(self, world: Any, event: Any) -> None:
        """Optional event handler. Default implementations do nothing."""
        ...


class BaseSystem:
    """Default base class. Concrete systems should set name/dependencies/emits/subscribes."""

    name: str = "unnamed"
    dependencies: list[str] = []
    emits: list[str] = []
    subscribes: list[str] = []

    def defaults(self, config: dict) -> dict:
        return {}

    def tick(self, world: Any, dt_years: float) -> None:
        pass

    def emit_snapshot(self, world: Any) -> dict:
        return dict(world.state.get(self.name, {}))

    def on_event(self, world: Any, event: Any) -> None:
        pass
