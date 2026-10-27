"""The synthetic subscriber base.

Subscribers are not stored: subscriber *i* is a pure function of ``(seed, i)``. Any index
yields the same person on every run, in every process, in any order, so a generator can
ask for "subscriber 4 217" without a table and the Spark job can later join on the same
identity.

Identifiers are built so that they cannot be real:

* MSISDN ``+223 0XXXXXXX``: a leading 0 is not a valid first digit of a subscriber number
  in the Malian plan, so the number cannot belong to anyone;
* IMSI ``61099...``: Mali's country code with a mobile network code that no operator holds
  (still shaped like a real IMSI, so the PII detector is exercised on realistic data);
* IMEI: a made-up 15-digit string.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from telecom_common.domain.enums import Technology
from telecom_common.reference import ReferenceCatalog
from telecomx_generators.events.rand import stream_rng
from telecomx_generators.reference.build import ANCHORS

__all__ = ["Population", "Subscriber", "anchor_site_id"]

_SEGMENTS = (("light", 0.55, 3_000), ("standard", 0.35, 10_000), ("heavy", 0.10, 30_000))
_PLANS = ("PREPAID-BASIC", "PREPAID-PLUS", "PREPAID-DATA", "POSTPAID-PRO")
#: How likely a region's cell is to be chosen as a home cell, by technology.
_TECH_AFFINITY = {
    Technology.G2: 0.10,
    Technology.G3: 0.25,
    Technology.G4: 0.60,
    Technology.G5: 0.05,
}


def anchor_site_id() -> str:
    """The site of the demonstration scenario (``SITE-102``)."""
    return f"SITE-{ANCHORS.degraded_site:03d}"


@dataclass(frozen=True, slots=True)
class Subscriber:
    """A synthetic subscriber. Only :attr:`msisdn`, :attr:`imsi` and :attr:`imei` are PII."""

    index: int
    msisdn: str
    imsi: str
    imei: str
    region: str
    home_cell: str
    segment: str
    typical_amount: int  # median Mobile Money amount, XOF
    plan: str


class Population:
    """A deterministic population drawn over the cells of a reference catalogue."""

    def __init__(self, catalog: ReferenceCatalog, tenant_id: str, seed: int, size: int) -> None:
        self._catalog = catalog
        self._tenant = tenant_id
        self._seed = seed
        self.size = size

        sites_region = {s.site_id: s.region_code for s in catalog.sites if s.tenant_id == tenant_id}
        regions = [r for r in catalog.regions if r.tenant_id == tenant_id]
        self._regions = [r.region_code for r in regions]
        self._region_cum: list[float] = []
        total = 0.0
        for region in regions:
            total += region.subscriber_count
            self._region_cum.append(total)

        # Home cells by region, weighted by capacity and technology. The cells of the
        # demonstration site are boosted: it models a dense commercial hub, which gives the
        # degradation scenario enough subscribers to complain about it.
        by_region: dict[str, list[tuple[str, float]]] = {code: [] for code in self._regions}
        for cell in catalog.cells:
            if cell.tenant_id != tenant_id or not cell.is_active:
                continue
            weight = cell.capacity_mb_per_hour * _TECH_AFFINITY[cell.technology]
            if cell.site_id == anchor_site_id():
                weight *= 40
            by_region[sites_region[cell.site_id]].append((cell.cell_id, weight))
        self._cells: dict[str, list[str]] = {}
        self._cell_cum: dict[str, list[float]] = {}
        for code, items in by_region.items():
            self._cells[code] = [c for c, _ in items]
            running, cumulative = 0.0, []
            for _, w in items:
                running += w
                cumulative.append(running)
            self._cell_cum[code] = cumulative

    @lru_cache(maxsize=65_536)  # noqa: B019 - the population lives as long as the generator
    def subscriber(self, index: int) -> Subscriber:
        """Subscriber ``index``, always the same person."""
        if not 0 <= index < self.size:
            msg = f"subscriber index {index} outside [0, {self.size})"
            raise IndexError(msg)
        rng = stream_rng(self._seed, "subscriber", index)
        region = rng.choices(self._regions, cum_weights=self._region_cum)[0]
        segment, _, amount = rng.choices(_SEGMENTS, weights=[s[1] for s in _SEGMENTS])[0]
        home = rng.choices(self._cells[region], cum_weights=self._cell_cum[region])[0]
        return Subscriber(
            index=index,
            msisdn=f"+2230{index:07d}",
            imsi=f"61099{rng.randrange(10**10):010d}",
            imei=f"35{rng.randrange(10**13):013d}",
            region=region,
            home_cell=home,
            segment=segment,
            typical_amount=amount,
            plan=rng.choice(_PLANS),
        )

    def pick(self, rng_choice: float) -> Subscriber:
        """A subscriber chosen by activity: ``rng_choice`` is a uniform draw in [0, 1).

        Squaring skews the choice towards low indices, so a minority of subscribers
        generate most of the traffic, as in real usage.
        """
        return self.subscriber(min(self.size - 1, int(self.size * rng_choice**2)))

    def cells_of(self, region: str) -> list[str]:
        """Home-cell candidates of a region."""
        return self._cells[region]

    def cell_in_region(self, region: str, rng_choice: float) -> str:
        """A cell of ``region`` chosen by weight from a uniform draw (for mobility)."""
        cumulative = self._cell_cum[region]
        target = rng_choice * cumulative[-1]
        low, high = 0, len(cumulative) - 1
        while low < high:
            middle = (low + high) // 2
            if cumulative[middle] < target:
                low = middle + 1
            else:
                high = middle
        return self._cells[region][low]
