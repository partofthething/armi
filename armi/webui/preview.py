# Copyright 2026 Ocean Atomics, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Block definitions turned into shapes a browser can draw.

This is a schematic of the input, not a picture of a constructed block. It works at cold input
dimensions and does not need materials to load, so a block that is halfway through being edited
still draws instead of raising. That is the whole point: you want to see the hole you just put in
the middle of a pin before the block is valid enough to construct.

Two things are worked out here that the browser could not do on its own:

Linked dimensions
    ``id: fuel.od`` means "start where the fuel stops". Same rule ARMI uses, same regex.

Where each component sits
    A block with a ``grid name`` lays its components out on that pin lattice, one copy per lattice
    position the component claims. A block without one is concentric, largest ring outward.
"""

import math
import re

# ``otherComponent.dimension``, the same form resolved in armi.reactor.components.component.
_LINK = re.compile(r"^\s*([^.\s][^.]*?)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")

# Outer and inner dimension for each drawable shape, and the radius convention it uses.
_OUTLINE = {
    "annulus": ("od", "id", "diameter"),
    "hexagon": ("op", "ip", "flatToFlat"),
    "square": ("widthOuter", "widthInner", "width"),
    "rectangle": ("lengthOuter", "lengthInner", "rectangle"),
    "triangle": ("base", None, "triangle"),
    "helix": ("od", "id", "diameter"),
}


class PreviewError(ValueError):
    """The block cannot be laid out as drawn."""


def resolveDimensions(componentDefs: dict) -> dict:
    """Replace every linked dimension with the number it points at.

    Parameters
    ----------
    componentDefs : dict
        Component name to its definition, as it appears under a block.

    Returns
    -------
    dict
        The same mapping with numeric dimensions, plus an ``_unresolved`` list on any component
        whose links could not be followed.
    """
    resolved = {name: dict(defn) for name, defn in componentDefs.items()}

    # Links can chain (clad.id -> gap.od -> fuel.od), so keep going until nothing more resolves.
    # Bound the passes by the component count; anything left after that is a cycle.
    for _pass in range(len(resolved) + 1):
        progressed = False
        for name, defn in resolved.items():
            for key, value in list(defn.items()):
                if not isinstance(value, str):
                    continue
                match = _LINK.match(value)
                if match is None:
                    continue
                target, dim = match.group(1), match.group(2)
                if target not in resolved:
                    continue
                other = resolved[target].get(dim)
                if isinstance(other, (int, float)) and not isinstance(other, bool):
                    defn[key] = float(other)
                    progressed = True
        if not progressed:
            break

    for name, defn in resolved.items():
        stuck = [key for key, value in defn.items() if isinstance(value, str) and _LINK.match(value)]
        if stuck:
            defn["_unresolved"] = sorted(stuck)
    return resolved


def _num(defn, key, default=None):
    value = defn.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def outlineOf(defn: dict, drawAs: str) -> dict:
    """The outer and inner extent of one component, in centimeters."""
    outer, inner, convention = _OUTLINE.get(drawAs, (None, None, "area"))
    shape = {"draw": drawAs, "convention": convention}
    if drawAs == "rectangle":
        shape["lengthOuter"] = _num(defn, "lengthOuter")
        shape["lengthInner"] = _num(defn, "lengthInner", 0.0)
        shape["widthOuter"] = _num(defn, "widthOuter")
        shape["widthInner"] = _num(defn, "widthInner", 0.0)
        span = shape["lengthOuter"] or shape["widthOuter"]
        shape["extent"] = max(shape["lengthOuter"] or 0.0, shape["widthOuter"] or 0.0) if span else None
    elif drawAs == "triangle":
        shape["base"] = _num(defn, "base")
        shape["height"] = _num(defn, "height")
        shape["extent"] = max(shape["base"] or 0.0, shape["height"] or 0.0) or None
    elif outer is not None:
        shape["outer"] = _num(defn, outer)
        shape["inner"] = _num(defn, inner, 0.0) if inner else 0.0
        shape["extent"] = shape["outer"]
    else:
        shape["area"] = _num(defn, "area")
        shape["extent"] = None

    if drawAs == "helix":
        shape["helixDiameter"] = _num(defn, "helixDiameter")
        shape["axialPitch"] = _num(defn, "axialPitch")
        # A helix wraps around something, so its footprint is the wrap circle plus the wire.
        if shape["helixDiameter"] and shape["outer"]:
            shape["extent"] = shape["helixDiameter"] + shape["outer"]

    # Holes punched through a solid shape, drawn as voids on top of it.
    holeOD = _num(defn, "holeOD") or _num(defn, "holeOP")
    if holeOD:
        shape["hole"] = {
            "outer": holeOD,
            "count": int(_num(defn, "nHoles", 1) or 1),
            "radius": _num(defn, "holeRadFromCenter", 0.0) or 0.0,
            "shape": "hexagon" if defn.get("holeOP") is not None else "annulus",
        }
    return shape


def _instanceCount(component) -> float:
    """How many copies of a component the block holds."""
    if component["latticeIDs"]:
        return float(len(component["positions"]))
    return float(component["mult"] or 1.0)


def _areaOf(outline: dict, count: float):
    """Cross sectional area of every copy of one component, in cm^2, or None if it has no shape."""
    draw = outline.get("draw")
    outer = outline.get("outer") or 0.0
    inner = outline.get("inner") or 0.0
    if draw in ("annulus",):
        return math.pi / 4.0 * (outer**2 - inner**2) * count
    if draw == "hexagon":
        return math.sqrt(3.0) / 2.0 * (outer**2 - inner**2) * count
    if draw == "square":
        return (outer**2 - inner**2) * count
    if draw == "rectangle":
        full = (outline.get("lengthOuter") or 0.0) * (outline.get("widthOuter") or 0.0)
        hole = (outline.get("lengthInner") or 0.0) * (outline.get("widthInner") or 0.0)
        return (full - hole) * count
    if draw == "triangle":
        return 0.5 * (outline.get("base") or 0.0) * (outline.get("height") or 0.0) * count
    if draw == "area":
        area = outline.get("area")
        return None if area is None else area * count
    return None


def _boundingArea(outline: dict):
    """Area enclosed by a component's outer edge, ignoring any bore through it.

    A duct or an assembly box is often written with equal inner and outer dimensions, so its own
    area is zero even though it marks the edge of the block. This is how the block's footprint is
    recovered from such a component.
    """
    draw = outline.get("draw")
    outer = outline.get("outer") or 0.0
    if draw in ("annulus", "helix"):
        return math.pi / 4.0 * outer**2
    if draw == "hexagon":
        return math.sqrt(3.0) / 2.0 * outer**2
    if draw == "square":
        return outer**2
    if draw == "rectangle":
        return (outline.get("lengthOuter") or 0.0) * (outline.get("widthOuter") or 0.0)
    return 0.0


def _composition(components, gridCells, gridGeom):
    """Area per component, and what is left over for the shape that derives its own.

    Reported because a reflector or a homogenized block has almost no radial geometry to look at:
    the drawing is nearly blank and the areas are the whole content of the input.
    """
    footprint = 0.0
    if gridCells and gridGeom:
        # A block on a pin lattice is as big as the lattice it fills.
        iStep = gridGeom.get("iStep", [1.0, 0.0])
        jStep = gridGeom.get("jStep", [0.0, 1.0])
        cellArea = abs(iStep[0] * jStep[1] - iStep[1] * jStep[0])
        footprint = cellArea * len(gridCells)

    known = 0.0
    derived = []
    for component in components:
        count = _instanceCount(component)
        area = _areaOf(component["outline"], count)
        component["count"] = count
        component["area"] = area
        footprint = max(footprint, _boundingArea(component["outline"]))
        if component["outline"]["draw"] == "fill":
            derived.append(component)
        elif area:
            known += area

    for component in derived:
        component["area"] = max(0.0, footprint - known) / len(derived) if footprint else None

    return {"footprint": footprint or None, "accounted": known}


def blockPreview(blockDef: dict, shapesByName: dict, gridCells=None, gridGeom=None) -> dict:
    """Lay a block out for drawing.

    Parameters
    ----------
    blockDef : dict
        One entry from the ``blocks:`` section.
    shapesByName : dict
        Component shape name to its catalog entry, from :py:func:`armi.webui.catalog.shapes`.
    gridCells : dict, optional
        ``"i,j"`` to lattice specifier for the block's pin lattice, if it has one.
    gridGeom : dict, optional
        The lattice layout, from :py:meth:`armi.webui.document.BlueprintsDocument.geometryOf`.

    Returns
    -------
    dict
        ``components`` in draw order (background first), each with its resolved outline and the
        lattice positions it occupies, plus the overall ``extent`` in centimeters.
    """
    componentDefs = {name: defn for name, defn in blockDef.items() if isinstance(defn, dict) and name != "grid name"}
    resolved = resolveDimensions(componentDefs)

    components = []
    for name, defn in resolved.items():
        shapeName = str(defn.get("shape", "")).strip()
        catalogEntry = shapesByName.get(shapeName.lower()) or shapesByName.get(shapeName)
        drawAs = (catalogEntry or {}).get("drawAs", "area")
        outline = outlineOf(defn, drawAs)
        latticeIDs = defn.get("latticeIDs")
        components.append(
            {
                "name": name,
                "shape": shapeName,
                "material": defn.get("material"),
                "flags": defn.get("flags"),
                "isotopics": defn.get("isotopics"),
                "mult": _num(defn, "mult", 1.0),
                "latticeIDs": [str(each) for each in latticeIDs] if latticeIDs else None,
                "origin": defn.get("origin"),
                "outline": outline,
                "unresolved": defn.get("_unresolved"),
            }
        )

    positions = _positionsByLatticeId(gridCells) if gridCells else {}
    for component in components:
        component["positions"] = _placeComponent(component, positions, gridGeom)

    # Filler first, since coolant and the like sit behind everything. Then biggest to smallest, so
    # that a pin's clad lands on its gap, and the gap on its fuel.
    components.sort(key=lambda comp: (0 if _isFiller(comp) else 1, -(comp["outline"].get("extent") or 0.0)))

    composition = _composition(components, gridCells, gridGeom)

    return {
        "components": components,
        "extent": _extentOf(components, gridGeom, gridCells),
        "hasLattice": bool(gridCells),
        **composition,
    }


def _isFiller(component) -> bool:
    return component["outline"]["draw"] in ("fill", "area")


def _positionsByLatticeId(gridCells: dict) -> dict:
    """Group lattice positions by the specifier sitting in them."""
    byId = {}
    for key, spec in gridCells.items():
        i, j = (int(part) for part in str(key).split(","))
        byId.setdefault(str(spec), []).append([i, j])
    return byId


def _placeComponent(component, positionsById, gridGeom):
    """Where the copies of one component sit, in centimeters from the block center."""
    if component["latticeIDs"] and gridGeom:
        iStep = gridGeom.get("iStep", [1.0, 0.0])
        jStep = gridGeom.get("jStep", [0.0, 1.0])
        out = []
        for latticeId in component["latticeIDs"]:
            for i, j in positionsById.get(latticeId, []):
                out.append([i * iStep[0] + j * jStep[0], i * iStep[1] + j * jStep[1]])
        return out
    origin = component.get("origin")
    if isinstance(origin, (list, tuple)) and len(origin) >= 2:
        return [[float(origin[0]), float(origin[1])]]
    return [[0.0, 0.0]]


def _extentOf(components, gridGeom, gridCells) -> float:
    """Half-width of the drawing area, in centimeters."""
    reach = 0.0
    for component in components:
        size = component["outline"].get("extent") or 0.0
        for x, y in component["positions"]:
            reach = max(reach, abs(x) + size / 2.0, abs(y) + size / 2.0)
    if gridCells and gridGeom:
        iStep = gridGeom.get("iStep", [1.0, 0.0])
        jStep = gridGeom.get("jStep", [0.0, 1.0])
        for key in gridCells:
            i, j = (int(part) for part in str(key).split(","))
            x = i * iStep[0] + j * jStep[0]
            y = i * iStep[1] + j * jStep[1]
            reach = max(reach, abs(x), abs(y))
    return reach or 1.0
