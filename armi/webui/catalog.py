"""
What the editor can offer as a choice.

Every dropdown in the browser is filled from here rather than from a list typed into JavaScript, so
a plugin that registers a new material or component shape shows up in the editor with no further
work. It is all read straight out of the running ARMI application.
"""

import importlib
import inspect

from armi import materials as materialsPkg
from armi import runLog
from armi.reactor import components, geometry
from armi.reactor.flags import Flags

# Dimensions every shape carries that are not really geometry, kept out of the geometry form.
_NON_GEOMETRIC_DIMS = frozenset({"mult", "modArea"})

# How the editor draws each shape. Anything not listed falls back to an area-only box.
_DRAW_AS = {
    "circle": "annulus",
    "sphere": "annulus",
    "hexagon": "hexagon",
    "filletedhexagon": "hexagon",
    "holedhexagon": "hexagon",
    "square": "square",
    "holedsquare": "square",
    "rectangle": "rectangle",
    "solidrectangle": "rectangle",
    "holedrectangle": "rectangle",
    "cube": "rectangle",
    "triangle": "triangle",
    "helix": "helix",
    "radialsegment": "sector",
    "differentialradialsegment": "sector",
    "derivedshape": "fill",
}


def materials(namespaceOrder=None) -> list:
    """Every material the application can build, in the order ARMI would resolve them.

    Parameters
    ----------
    namespaceOrder : list of str, optional
        Module paths to search, as in the ``materialNamespaceOrder`` setting. Defaults to whatever
        the running application is already using.
    """
    namespaceOrder = namespaceOrder or materialsPkg.getMaterialNamespaceOrder()
    found = {}
    for namespace in namespaceOrder:
        try:
            module = importlib.import_module(namespace)
            classes = list(materialsPkg.iterAllMaterialClassesInNamespace(module))
        except Exception as ee:
            # A namespace the user has not installed should grey out its own materials, not take
            # the whole catalog down with it.
            runLog.debug(f"Skipping material namespace {namespace}: {ee}")
            continue
        for cls in classes:
            # First namespace to claim a name wins, which is the rule ARMI resolves names by.
            found.setdefault(
                cls.__name__,
                {
                    "name": cls.__name__,
                    "namespace": namespace,
                    "qualified": f"{cls.__module__}:{cls.__name__}",
                    "doc": _firstLine(cls.__doc__),
                },
            )
    materialList = sorted(found.values(), key=lambda mat: mat["name"].lower())
    materialList.insert(0, {"name": "Void", "namespace": "armi.materials", "qualified": "Void", "doc": "Nothing."})
    return materialList


def shapes() -> list:
    """Every component shape, with the dimensions it takes and how to draw it."""
    out = []
    for name, cls in sorted(components.ComponentType.TYPES.items()):
        dims = tuple(getattr(cls, "DIMENSION_NAMES", ()) or ())
        if not dims and name not in ("unshapedcomponent", "derivedshape"):
            # Abstract bases like `component` and `shapedcomponent` are not selectable.
            continue
        out.append(
            {
                "name": cls.__name__,
                "key": name,
                "dimensions": [dim for dim in dims if dim not in _NON_GEOMETRIC_DIMS],
                "takesMult": "mult" in dims,
                "drawAs": _DRAW_AS.get(name, "area"),
                "doc": _firstLine(cls.__doc__),
            }
        )
    return out


def flags() -> list:
    """Every flag name that can appear in a ``flags:`` entry."""
    return sorted(Flags.sortedFields())


def geometries() -> list:
    """The grid geometries and the symmetries each one allows."""
    out = []
    for geom in (geometry.HEX, geometry.HEX_CORNERS_UP, geometry.CARTESIAN):
        geomType = geometry.GeomType.fromStr(geom)
        # GeomType folds corners-up into plain HEX, so orientation stays a string comparison.
        cornersUp = geom == geometry.HEX_CORNERS_UP
        allowed = []
        for domain, boundary in geometry.VALID_GEOM_SYMMETRY[geomType]:
            allowed.append(str(geometry.SymmetryType(domain, boundary)))
            # Some domains can additionally sit with an assembly straddling the centerline.
            # SymmetryType raises on a combination it does not like, so ask before building one.
            if (domain, boundary, True) in geometry.SymmetryType.VALID_SYMMETRY:
                allowed.append(str(geometry.SymmetryType(domain, boundary, throughCenterAssembly=True)))
        out.append(
            {
                "geom": str(geom),
                "shape": "hex" if geomType == geometry.GeomType.HEX else "square",
                "cornersUp": cornersUp,
                "pitchKeys": ["hex"] if geomType != geometry.GeomType.CARTESIAN else ["x", "y"],
                "symmetries": sorted(set(allowed)),
            }
        )
    return out


def everything(namespaceOrder=None) -> dict:
    """The whole catalog, which the editor fetches once at startup."""
    return {
        "materials": materials(namespaceOrder),
        "shapes": shapes(),
        "flags": flags(),
        "geometries": geometries(),
    }


def _firstLine(doc) -> str:
    if not doc:
        return ""
    return inspect.cleandoc(doc).split("\n", 1)[0].strip()
