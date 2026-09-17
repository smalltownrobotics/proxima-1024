"""World — the shared state container.

Holds per-system state slices, the deterministic RNG, the event bus, the
config, and the tick clock. Slices are addressed by system name.

Slice ownership is enforced in dev mode via `World.write()`: a system may only
write to its own slice. Reading any slice is allowed via `World.read()` or
direct `world.state[...]` access.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from .events import EventBus
from .rng import DeterministicRng


@dataclasses.dataclass
class World:
    config: dict                                   # resolved, immutable for the run
    rng: DeterministicRng
    events: EventBus
    tick: int = 0
    sim_year: float = 0.0
    earth_year: float = 0.0
    state: dict[str, dict] = dataclasses.field(default_factory=dict)
    _writing_system: str | None = None              # set by orchestrator during a system's tick
    _strict_ownership: bool = True

    # ---- slice access ----
    def alloc(self, system_name: str, initial: dict) -> None:
        """Allocate a new slice for a system."""
        if system_name in self.state:
            raise ValueError(f"slice {system_name} already allocated")
        self.state[system_name] = dict(initial)

    def read(self, path: str) -> Any:
        """Read a dotted path: 'system.field' or 'system.field.sub'."""
        parts = path.split(".")
        cur: Any = self.state
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
        return cur

    def write(self, path: str, value: Any) -> None:
        """Write a dotted path. Enforces slice ownership in strict mode."""
        parts = path.split(".")
        if len(parts) < 2:
            raise ValueError(f"write path must be 'system.field' or deeper: {path}")
        system_name = parts[0]
        if self._strict_ownership and self._writing_system is not None and system_name != self._writing_system:
            raise SliceOwnershipError(
                f"system '{self._writing_system}' may not write to slice '{system_name}' "
                f"(path={path}). Emit an event instead."
            )
        if system_name not in self.state:
            raise KeyError(f"unknown slice: {system_name}")
        cur: Any = self.state[system_name]
        for p in parts[1:-1]:
            if isinstance(cur, dict):
                if p not in cur:
                    cur[p] = {}
                cur = cur[p]
            elif isinstance(cur, list):
                cur = cur[int(p)]
            else:
                raise TypeError(f"cannot traverse {type(cur)} at {p}")
        cur[parts[-1]] = value


class SliceOwnershipError(RuntimeError):
    """Raised when a system tries to write to another system's slice in strict mode."""
