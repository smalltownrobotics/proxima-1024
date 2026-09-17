"""DeterministicRng — seeded random with per-system forking.

Each system requests a forked sub-generator keyed by its system name. Adding a
new system does not perturb existing systems' draws because each fork is its
own independent stream seeded from (parent_seed, system_name).

Backed by random.Random under the hood. Uses xoshiro semantics conceptually
(deterministic across machines, no state leakage between forks).
"""
from __future__ import annotations

import hashlib
import random
import math


class DeterministicRng:
    """Top-level RNG. Forks produce independent streams keyed by name."""

    __slots__ = ("seed", "_rng", "_forks")

    def __init__(self, seed: int):
        self.seed = int(seed)
        self._rng = random.Random(self.seed)
        self._forks: dict[str, "DeterministicRng"] = {}

    def fork(self, name: str) -> "DeterministicRng":
        """Return a sub-RNG seeded from (self.seed, name). Cached: same name returns same fork."""
        if name in self._forks:
            return self._forks[name]
        h = hashlib.sha256(f"{self.seed}::{name}".encode()).digest()
        sub_seed = int.from_bytes(h[:8], "big")
        sub = DeterministicRng(sub_seed)
        self._forks[name] = sub
        return sub

    # --- common draws (delegate to underlying random.Random) ---
    def random(self) -> float:
        return self._rng.random()

    def uniform(self, lo: float, hi: float) -> float:
        return self._rng.uniform(lo, hi)

    def randint(self, lo: int, hi: int) -> int:
        return self._rng.randint(lo, hi)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rng.gauss(mu, sigma)

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam > 30:
            # normal approximation for large lambda
            return max(0, int(self._rng.gauss(lam, math.sqrt(lam)) + 0.5))
        L = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            k += 1
            p *= self._rng.random()
            if p <= L:
                return k - 1

    def choice(self, seq):
        return self._rng.choice(seq)

    def shuffle(self, seq) -> None:
        self._rng.shuffle(seq)
