"""
Spherical blocks: a pebble and its share of the bed around it, as a spherical unit cell.

A pebble bed's unit cell is the classic lattice-physics model of pebble fuel: concentric spheres for the fuel zone
(with its dispersed particles), the fuel-free shell, and an outer shell that smears the pebble's share of the
surroundings (moderator pebbles, coolant, voids) by volume. A transport code closes it with a white boundary.

ARMI's data model is extruded (component areas times the block height), so a spherical block keeps it: each
:py:class:`~armi.reactor.components.volumetricShapes.Sphere` reports its volume over the block height as its area.
The block height is then only a bookkeeping length; the volumes, and so every number density and mass, are those of
the spheres.
"""

import math

from armi.reactor import components
from armi.reactor.blocks.block import Block


class SphericalBlock(Block):
    """A spherical unit cell; its outermost sphere is its boundary and its diameter is its pitch."""

    PITCH_DIMENSION = "od"
    PITCH_COMPONENT_TYPE = components.Sphere

    def getMaxArea(self):
        """Volume of the outermost sphere over the block height: the block's area if it were full."""
        return math.pi / 6.0 * self.getPitch() ** 3 / self.getHeight()

    def setPitch(self, val, updateBolParams=False):
        raise NotImplementedError("Set the diameter of a spherical block's outer sphere instead.")
