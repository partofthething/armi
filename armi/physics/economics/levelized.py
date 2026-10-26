r"""
Levelized costs: the price of energy that pays back a stream of costs at a discount rate.

.. math::

    \text{LCOE} = \frac{\sum_i C_i (1 + r)^{-t_i}}{\sum_j E_j (1 + r)^{-t_j}}

Costs and generation are each a list of ``(time in years, amount)`` pairs, so any cost stream (fuel reloads now,
construction and operations later) levelizes the same way and the results add up.
"""

from dataclasses import dataclass
from typing import Sequence

from armi.utils import getAvailabilityFactors, getCycleLengths, getPowerFractions

DAYS_PER_YEAR = 365.25

CashFlow = Sequence[tuple[float, float]]


def presentValue(flows: CashFlow, discountRate: float) -> float:
    """Return the sum of ``(year, amount)`` flows discounted to year zero."""
    return sum(amount / (1.0 + discountRate) ** year for year, amount in flows)


def levelizedCost(costs: CashFlow, generation: CashFlow, discountRate: float) -> float:
    """Return the levelized cost: discounted costs over discounted generation (e.g. $/MWh)."""
    return presentValue(costs, discountRate) / presentValue(generation, discountRate)


@dataclass(frozen=True)
class Cycle:
    """One operating cycle of the plant."""

    start: float
    """Start, in years from first startup."""

    length: float
    """Calendar length, in years."""

    capacityFactor: float
    """Energy made over the energy at full power for the whole calendar length."""

    @property
    def midpoint(self) -> float:
        return self.start + self.length / 2.0


def cyclesFromSettings(cs) -> list[Cycle]:
    """
    Return the plant's operating cycles from the case settings.

    A cycle's calendar length is ``cycleLength(s)``, and the plant runs at full power for ``availabilityFactor(s)`` of
    it, at the mean of its ``powerFractions``.
    """
    cycles = []
    start = 0.0
    for days, availability, powerFractions in zip(
        getCycleLengths(cs), getAvailabilityFactors(cs), getPowerFractions(cs)
    ):
        meanPower = sum(powerFractions) / len(powerFractions) if powerFractions else 1.0
        cycles.append(Cycle(start, days / DAYS_PER_YEAR, availability * meanPower))
        start += days / DAYS_PER_YEAR
    return cycles


def generationByCycle(cycles: Sequence[Cycle], electricPowerMW: float) -> list[tuple[float, float]]:
    """Return each cycle's net generation in MWh, credited at the cycle's midpoint."""
    hoursPerYear = DAYS_PER_YEAR * 24.0
    return [(c.midpoint, electricPowerMW * c.capacityFactor * c.length * hoursPerYear) for c in cycles]
