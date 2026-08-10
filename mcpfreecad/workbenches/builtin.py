"""Built-in helper implementations that only depend on core FreeCAD modules."""

from __future__ import annotations

import math
from typing import Any, Dict

from mcpfreecad.workbenches.base import HelperSpec


def _helper_threaded_rod(App, Gui, Part, document, params: Dict[str, Any]):
    del Gui
    radius = float(params.get("radius", 4.0))
    height = float(params.get("height", 20.0))
    pitch = float(params.get("pitch", 1.25))
    depth = float(params.get("depth", 0.5))
    profile_radius = max(0.05, depth)

    helix = Part.makeHelix(pitch, height, radius)
    profile = Part.makeCircle(profile_radius, App.Vector(radius, 0, 0), App.Vector(0, 1, 0))
    wire = Part.Wire(profile.Edges)
    shell = Part.Wire(helix.Edges)
    thread_shape = shell.makePipeShell([wire], True, True)

    core = Part.makeCylinder(max(0.1, radius - depth), height)
    shape = core.fuse(thread_shape)

    feature = document.addObject("Part::Feature", params.get("name", "ThreadedRod"))
    feature.Shape = shape
    return feature


def _helper_spur_gear(App, Gui, Part, document, params: Dict[str, Any]):
    del Gui
    teeth = int(params.get("teeth", 16))
    module = float(params.get("module", 2.0))
    thickness = float(params.get("thickness", 8.0))
    bore_diameter = float(params.get("bore_diameter", 5.0))
    outer_radius = module * (teeth + 2) / 2.0
    root_radius = max(module, module * (teeth - 2.5) / 2.0)
    tooth_angle = (2.0 * math.pi) / teeth
    flank_ratio = 0.35

    face = Part.makeCircle(root_radius).toShape()
    base = Part.Face(Part.Wire([face]))
    gear = base.extrude(App.Vector(0, 0, thickness))

    for index in range(teeth):
        center = index * tooth_angle
        left = center - tooth_angle * flank_ratio
        right = center + tooth_angle * flank_ratio
        points = [
            App.Vector(root_radius * math.cos(left), root_radius * math.sin(left), 0),
            App.Vector(outer_radius * math.cos(left), outer_radius * math.sin(left), 0),
            App.Vector(outer_radius * math.cos(right), outer_radius * math.sin(right), 0),
            App.Vector(root_radius * math.cos(right), root_radius * math.sin(right), 0),
        ]
        polygon = Part.makePolygon(points + [points[0]])
        tooth = Part.Face(Part.Wire(polygon.Edges)).extrude(App.Vector(0, 0, thickness))
        gear = gear.fuse(tooth)

    if bore_diameter > 0:
        bore = Part.makeCylinder(bore_diameter / 2.0, thickness)
        gear = gear.cut(bore)

    feature = document.addObject("Part::Feature", params.get("name", "SpurGear"))
    feature.Shape = gear
    return feature


def get_helper_specs() -> list[HelperSpec]:
    return [
        HelperSpec(
            name="threaded_rod",
            source="builtin",
            description="Create a helical threaded rod approximation using core Part geometry.",
            gui_required=False,
            available=True,
            runner=_helper_threaded_rod,
        ),
        HelperSpec(
            name="spur_gear",
            source="builtin",
            description="Create a simple spur gear approximation without optional workbenches.",
            gui_required=False,
            available=True,
            runner=_helper_spur_gear,
        ),
    ]
