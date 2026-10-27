"""Reproducible randomness for event generation.

One independent stream per ``(seed, part, ...)`` tuple, never a shared global generator.
That is what makes bucketed generation composable: the events of minute *m* depend only
on ``(seed, stream, m)``, not on how many events were drawn before. Generating a window
in one go or in slices therefore gives the same events.

Determinism holds for one Python minor version (the standard library does not promise
identical streams across minor versions), which the repository pins to 3.11.
"""

from __future__ import annotations

import math
import random

__all__ = ["bounded_lognormal", "poisson", "stream_rng"]


def stream_rng(seed: int, *parts: object) -> random.Random:
    """An independent generator for ``(seed, *parts)``."""
    return random.Random(":".join(str(p) for p in (seed, *parts)))  # noqa: S311 - synthetic data


def poisson(rng: random.Random, lam: float) -> int:
    """Draw a Poisson-distributed count with mean ``lam``.

    Knuth's method for small means, a rounded normal approximation above 30 (where the
    two agree closely and Knuth's loop becomes slow and numerically fragile).
    """
    if lam <= 0:
        return 0
    if lam < 30:
        threshold = math.exp(-lam)
        count, product = 0, rng.random()
        while product > threshold:
            count += 1
            product *= rng.random()
        return count
    return max(0, round(rng.gauss(lam, math.sqrt(lam))))


def bounded_lognormal(
    rng: random.Random, median: float, sigma: float, low: float, high: float
) -> float:
    """A log-normal draw with the given median, clamped to ``[low, high]``."""
    return max(low, min(high, rng.lognormvariate(math.log(median), sigma)))
