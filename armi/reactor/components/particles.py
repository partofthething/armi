"""
Particles dispersed in a host component: TRISO fuel, burnable poison grains, and the like.

A dispersed-particle component is described by its host (the compact, pebble zone or plate it sits in, whose own
material is the matrix) and a stack of spherical layers. Each layer becomes a :py:class:`ParticleLayer` component in
the same block as its host, so everything that works on a block's components -- number densities, masses,
homogenization, depletion, the database -- sees every layer as an ordinary component with its own material,
temperature and composition.

The trouble with modeling particles as spheres in an extruded (area times height) data model is that a sphere's volume
does not grow with the block's height. A ``ParticleLayer`` therefore counts particles *per centimeter of block height*:
its ``mult`` is the number of particles in one centimeter of its block, and its area, in cm^2, is that count times the
volume of one spherical shell. Volume, mass and every number density then scale with block height exactly as an
extruded component's do.

A host that is itself three-dimensional (a :py:class:`~armi.reactor.components.volumetricShapes.Sphere`, such as the
fuel zone of a pebble) has a volume that does not scale with height either. Its particles are then three-dimensional
too: ``mult`` is the number of particles, and the area of a layer is its volume over the block height, as a sphere's
is.

The particle count is fixed at construction from the packing fraction and the cold dimensions of host and particle,
so it does not change as the host or the particles expand. The host's area is its outline less the particles inside
it: the host *is* the matrix, and the matrix plus its particles fill the host outline.
"""

import math

from armi.reactor.components import ShapedComponent, componentParameters


def sphereVolume(od, iD=0.0):
    """Volume (cm^3) of a spherical shell of outer and inner diameters ``od`` and ``iD``."""
    return math.pi / 6.0 * (od**3 - iD**3)


class ParticleLayer(ShapedComponent):
    """
    One spherical layer of the particles dispersed in a host component.

    Parameters
    ----------
    od, id : float
        Outer and inner diameters of the layer, in cm (``id`` is zero for a kernel).
    mult : float
        Particles per centimeter of block height, summed over every copy of the host in the block; or, when the host
        is three-dimensional, the number of particles.
    """

    is3D = False

    THERMAL_EXPANSION_DIMS = {"od", "id"}

    pDefs = componentParameters.getCircleParameterDefinitions()

    def __init__(
        self,
        name,
        material,
        Tinput,
        Thot,
        od,
        id=0.0,
        mult=1.0,
        modArea=None,
        isotopics=None,
        mergeWith=None,
        components=None,
    ):
        ShapedComponent.__init__(
            self,
            name,
            material,
            Tinput,
            Thot,
            isotopics=isotopics,
            mergeWith=mergeWith,
            components=components,
        )
        self._linkAndStoreDimensions(components, od=od, id=id, mult=mult, modArea=modArea)
        #: The component the particles are dispersed in; set when the particle fuel is built.
        self.host = None

    def getBoundingCircleOuterDiameter(self, Tc=None, cold=False):
        return self.getDimension("od", cold=cold, Tc=Tc)

    def getComponentArea(self, cold=False, Tc=None):
        """Area (cm^2): particles per cm of block height times the volume of one shell."""
        od = self.getDimension("od", cold=cold, Tc=Tc)
        iD = self.getDimension("id", cold=cold, Tc=Tc)
        area = self.getDimension("mult") * sphereVolume(od, iD)
        return area / self._blockHeight() if self.is3D else area

    def getComponentVolume(self, cold=False):
        """Volume (cm^3) of every particle's shell of this layer (for a layer in a three-dimensional host)."""
        od = self.getDimension("od", cold=cold)
        iD = self.getDimension("id", cold=cold)
        return self.getDimension("mult") * sphereVolume(od, iD)

    def _blockHeight(self):
        from armi.reactor.blocks import Block  # avoid circular import

        return self.getAncestor(lambda c: isinstance(c, Block)).getHeight()

    def getParticleCount(self, height=None):
        """Number of particles in the block (or in ``height`` cm of it)."""
        if self.is3D:
            return self.getDimension("mult")
        if height is None:
            height = self.parent.getHeight() if self.parent is not None else 1.0
        return self.getDimension("mult") * height


def attachParticleLayers(host, layers, packingFraction):
    """
    Disperse particles made of ``layers`` in ``host`` at ``packingFraction``.

    Sets each layer's ``mult`` (particles per cm of block height, or particles if the host is three-dimensional) from
    the packing fraction and the *cold* volumes of host and particle, links the layers to the host, and makes the
    host's area its outline less its particles.

    Parameters
    ----------
    host : Component
        The component the particles are dispersed in. Its material is the matrix.
    layers : list of ParticleLayer
        The particle's layers, innermost first.
    packingFraction : float
        Volume fraction of the host outline the particles fill, at the input (cold) dimensions.
    """
    packingFraction = float(packingFraction)  # not the YAML parser's float subclass, which the DB cannot store
    if not 0.0 < packingFraction < 0.75:
        raise ValueError(f"Packing fraction of particles in {host} must be in (0, 0.75), not {packingFraction}")
    particleVolume = sphereVolume(layers[-1].getDimension("od", cold=True))
    if host.is3D:
        hostVolume = host.getComponentVolume(cold=True)  # all copies of the host
    else:
        hostVolume = host.getComponentArea(cold=True)  # all copies of the host, per cm of height
    count = packingFraction * hostVolume / particleVolume
    for layer in layers:
        layer.is3D = host.is3D
        layer.setDimension("mult", count)
        layer.host = host
    host.particleLayers = list(layers)
    host.p.packingFraction = packingFraction
    return count
