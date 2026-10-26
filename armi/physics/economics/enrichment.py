r"""
Uranium enrichment: the feed, tails and separative work it takes to make enriched product.

A cascade splits a feed stream :math:`F` at assay :math:`x_f` into product :math:`P` at :math:`x_p` and tails
:math:`T` at :math:`x_t`. Mass and U-235 balances fix the feed per unit product,

.. math::

    \frac{F}{P} = \frac{x_p - x_t}{x_f - x_t},

and the separative work per unit product follows from the value function :math:`V(x) = (1 - 2x)\ln\frac{1-x}{x}`,

.. math::

    \frac{S}{P} = V(x_p) + \frac{T}{P} V(x_t) - \frac{F}{P} V(x_f).

Assays are U-235 mass fractions of uranium (0.05 for 5 wt%), masses are kg of uranium, and separative work is in
kg-SWU.

Product below the natural assay is not enriched: it is natural uranium blended down with tails, which takes feed but
no separative work. Product at or below the tails assay comes from the depleted uranium stockpile and takes neither.
"""

import math
from dataclasses import dataclass

from scipy.optimize import minimize_scalar

#: U-235 mass fraction of natural uranium.
NATURAL_ASSAY = 0.00711

#: A typical tails assay for current Western enrichment plants.
DEFAULT_TAILS_ASSAY = 0.0025

#: kg of U3O8 per kg of uranium in it (natural uranium, 238.0289 g/mol, and oxygen, 15.999 g/mol).
U3O8_PER_U = (3 * 238.0289 + 8 * 15.999) / (3 * 238.0289)

#: Pounds per kilogram.
LB_PER_KG = 2.20462


def valueFunction(assay: float) -> float:
    r"""Return the separation potential :math:`V(x) = (1 - 2x)\ln((1 - x)/x)` of a stream at ``assay``."""
    return (1.0 - 2.0 * assay) * math.log((1.0 - assay) / assay)


def feedFactor(productAssay: float, feedAssay: float = NATURAL_ASSAY, tailsAssay: float = DEFAULT_TAILS_ASSAY):
    """Return kg of feed per kg of product."""
    _checkAssays(feedAssay, tailsAssay)
    if productAssay <= tailsAssay:
        return 0.0
    return (productAssay - tailsAssay) / (feedAssay - tailsAssay)


def swuFactor(productAssay: float, feedAssay: float = NATURAL_ASSAY, tailsAssay: float = DEFAULT_TAILS_ASSAY):
    """Return kg-SWU of separative work per kg of product."""
    if productAssay <= feedAssay:
        return 0.0
    ff = feedFactor(productAssay, feedAssay, tailsAssay)
    vt = valueFunction(tailsAssay)
    return valueFunction(productAssay) - vt - ff * (valueFunction(feedAssay) - vt)


@dataclass(frozen=True)
class EnrichmentRequirement:
    """Feed, tails and separative work to make ``product`` kg of uranium at ``productAssay``."""

    product: float
    productAssay: float
    feedAssay: float
    tailsAssay: float
    feed: float
    swu: float

    @property
    def tails(self) -> float:
        """Mass of tails left over, kg (negative when tails are blended into sub-natural product)."""
        return self.feed - self.product


def enrich(
    product: float,
    productAssay: float,
    feedAssay: float = NATURAL_ASSAY,
    tailsAssay: float = DEFAULT_TAILS_ASSAY,
) -> EnrichmentRequirement:
    """
    Return the feed and separative work needed for ``product`` kg of uranium at ``productAssay``.

    Examples
    --------
    >>> req = enrich(1.0, 0.045)
    >>> round(req.feed, 2), round(req.swu, 2)
    (9.22, 6.87)
    """
    return EnrichmentRequirement(
        product=product,
        productAssay=productAssay,
        feedAssay=feedAssay,
        tailsAssay=tailsAssay,
        feed=product * feedFactor(productAssay, feedAssay, tailsAssay),
        swu=product * swuFactor(productAssay, feedAssay, tailsAssay),
    )


def feedCostPerKgU(orePerLbU3O8: float, conversionPerKgU: float = 0.0, conversionLoss: float = 0.0) -> float:
    """
    Return the cost of one kg of uranium delivered to the enrichment plant as UF6.

    Uranium is bought as U3O8, priced per pound of U3O8, and converted to UF6, priced per kg of uranium.
    ``conversionLoss`` is the fraction of the uranium lost in conversion, which has to be bought but never arrives.
    """
    return orePerLbU3O8 * U3O8_PER_U * LB_PER_KG / (1.0 - conversionLoss) + conversionPerKgU


def optimumTailsAssay(
    feedCost: float,
    swuCost: float,
    feedAssay: float = NATURAL_ASSAY,
    productAssay: float = 0.05,
) -> float:
    """
    Return the tails assay that makes enriched product cheapest.

    Running the tails leaner buys back feed with separative work, so the optimum depends only on the ratio of the
    feed price (``feedCost``, per kg of uranium as UF6, see :py:func:`feedCostPerKgU`) to the SWU price. It barely
    depends on the product assay, which is here only to evaluate the cost.
    """

    def costPerKgProduct(tailsAssay):
        return feedFactor(productAssay, feedAssay, tailsAssay) * feedCost + swuCost * swuFactor(
            productAssay, feedAssay, tailsAssay
        )

    result = minimize_scalar(costPerKgProduct, bounds=(1e-4, feedAssay - 1e-5), method="bounded")
    return float(result.x)


def _checkAssays(feedAssay, tailsAssay):
    if not 0.0 < tailsAssay < feedAssay < 1.0:
        raise ValueError(f"Need 0 < tails assay ({tailsAssay}) < feed assay ({feedAssay}) < 1.")
