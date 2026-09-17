"""Registry + tick orchestrator.

Holds the list of registered systems, computes their tick order via topological
sort over `dependencies`, and runs the per-tick lifecycle:

  for each system in tick_order:
      world._writing_system = system.name
      system.tick(world, dt)

  world.events.dispatch_pending(world)   # apply effects + run subscribers

  for each system:
      snapshot[system.name] = system.emit_snapshot(world)
  trajectory.append(snapshot)

Cycles in the dependency graph raise at registration time.
"""
from __future__ import annotations

from typing import Any

from .events import EventBus, Event
from .rng import DeterministicRng
from .system import System
from .world import World


class Registry:
    __slots__ = ("_systems", "_order")

    def __init__(self) -> None:
        self._systems: dict[str, System] = {}
        self._order: list[str] = []

    def register(self, system: System) -> None:
        if system.name in self._systems:
            raise ValueError(f"system already registered: {system.name}")
        self._systems[system.name] = system
        self._order = self._toposort()

    def _toposort(self) -> list[str]:
        """Return system names in dependency order. Raises on cycles."""
        order: list[str] = []
        visited: set[str] = set()
        in_progress: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in in_progress:
                raise ValueError(f"dependency cycle through {name}")
            if name not in self._systems:
                # depend on a not-yet-registered system; defer (it'll show on its own visit)
                return
            in_progress.add(name)
            for dep in self._systems[name].dependencies:
                visit(dep)
            in_progress.remove(name)
            visited.add(name)
            order.append(name)

        for name in self._systems:
            visit(name)
        return order

    def order(self) -> list[str]:
        return list(self._order)

    def systems(self) -> list[System]:
        return [self._systems[n] for n in self._order]

    def get(self, name: str) -> System:
        return self._systems[name]


class Orchestrator:
    """Runs the sim. Owns the world + registry."""

    def __init__(self, registry: Registry, world: World, dt_years: float = 1.0):
        self.registry = registry
        self.world = world
        self.dt_years = dt_years
        # Subscribe systems' on_event handlers
        for sys_ in registry.systems():
            for kind in sys_.subscribes:
                world.events.subscribe(kind, sys_.on_event)

    def initialize(self) -> None:
        """Allocate each system's slice from its defaults()."""
        for sys_ in self.registry.systems():
            self.world.alloc(sys_.name, sys_.defaults(self.world.config))

    def step(self) -> dict:
        """Advance one tick. Returns the snapshot."""
        self.world.tick += 1
        self.world.sim_year += self.dt_years
        self.world.earth_year += self.dt_years
        # Tick each system (in dependency order) under its writing-context
        for sys_ in self.registry.systems():
            self.world._writing_system = sys_.name
            try:
                sys_.tick(self.world, self.dt_years)
            finally:
                self.world._writing_system = None
        # Dispatch all events emitted during this tick
        dispatched = self.world.events.dispatch_pending(self.world)
        # Build snapshot
        snapshot: dict = {
            "tick": self.world.tick,
            "sim_year": self.world.sim_year,
            "earth_year": self.world.earth_year,
            "systems": {},
            "events": [e.to_dict() for e in dispatched],
        }
        for sys_ in self.registry.systems():
            snapshot["systems"][sys_.name] = sys_.emit_snapshot(self.world)
        return snapshot

    def run(self, n_ticks: int) -> list[dict]:
        """Run n_ticks and return the trajectory list."""
        traj: list[dict] = []
        # initial state snapshot at tick 0
        snap0 = {
            "tick": 0,
            "sim_year": 0.0,
            "earth_year": float(self.world.earth_year),
            "systems": {},
            "events": [],
        }
        for sys_ in self.registry.systems():
            snap0["systems"][sys_.name] = sys_.emit_snapshot(self.world)
        traj.append(snap0)
        for _ in range(n_ticks):
            traj.append(self.step())
        return traj


def build_world(config: dict, seed: int | None = None) -> World:
    seed = seed if seed is not None else int(config.get("tunables", {}).get("rng_seed", 42))
    return World(
        config=config,
        rng=DeterministicRng(seed),
        events=EventBus(),
        tick=0,
        sim_year=0.0,
        earth_year=float(config.get("mission", {}).get("launch_year", 2150)),
        state={},
    )
