"""
Composable parameter sweeps for building case suites.

A :py:class:`Sweep` is an ordered collection of points, each point a tuple of
:py:class:`~armi.cases.inputModifiers.inputModifiers.InputModifier` objects that together define one case. Sweeps are
built from axes and combined with a small algebra, so mixed shapes read the way they are described::

    from armi.cases.sweeps import Sweep

    power = Sweep.axis(SettingsModifier("power", p) for p in (1.0e8, 2.0e8, 3.0e8))
    cycle = Sweep.axis(SettingsModifier("cycleLength", c) for c in (300, 400))
    nominal = Sweep.point(SettingsModifier("comment", "nominal"))

    suite = nominal + power * cycle  # 1 + 6 cases
    short = suite.where(lambda v: v.get("cycleLength", 0) < 400)  # keep the shorter cycles

``*`` is the Cartesian product (the left operand varies slowest), ``+`` concatenates, :py:meth:`Sweep.zip` pairs
points one to one and :py:meth:`Sweep.where` keeps the points whose independent variables satisfy a predicate.

Hand a sweep to :py:meth:`~armi.cases.suiteBuilder.SuiteBuilder.addSweep` to turn it into cases.
"""

import itertools
from typing import Callable, Iterable, Iterator, Tuple

Point = Tuple  # a tuple of InputModifier


class Sweep:
    """An ordered collection of points, each a tuple of input modifiers that define one case."""

    def __init__(self, points: Iterable[Point] = ((),)):
        self.points = [tuple(p) for p in points]

    @classmethod
    def axis(cls, modifiers: Iterable) -> "Sweep":
        """One point per modifier: a single degree of freedom."""
        return cls((m,) for m in modifiers)

    @classmethod
    def point(cls, *modifiers) -> "Sweep":
        """A single point made of all ``modifiers`` together."""
        return cls([modifiers])

    def __iter__(self) -> Iterator[Point]:
        return iter(self.points)

    def __len__(self):
        return len(self.points)

    def __repr__(self):
        return f"<Sweep of {len(self)} points>"

    def __mul__(self, other: "Sweep") -> "Sweep":
        """The Cartesian product, with ``self`` varying slowest."""
        return Sweep(a + b for a, b in itertools.product(self.points, other.points))

    def __add__(self, other: "Sweep") -> "Sweep":
        """The points of ``self`` followed by those of ``other``."""
        return Sweep(self.points + other.points)

    def zip(self, other: "Sweep") -> "Sweep":
        """Pair points one to one, e.g. to move two parameters together."""
        if len(self) != len(other):
            raise ValueError(f"Cannot zip sweeps of {len(self)} and {len(other)} points")
        return Sweep(a + b for a, b in zip(self.points, other.points))

    def where(self, predicate: Callable[[dict], bool]) -> "Sweep":
        """The points whose merged independent variables satisfy ``predicate``."""
        return Sweep(p for p in self.points if predicate(independentVariables(p)))


def independentVariables(point: Point) -> dict:
    """The independent variables of a point's modifiers, merged in order."""
    merged = {}
    for modifier in point:
        merged.update(modifier.independentVariable)
    return merged
