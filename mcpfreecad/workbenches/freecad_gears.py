"""Adapter for the optional freecad.gears workbench."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any, Dict

from mcpfreecad.workbenches.base import HelperSpec


GEAR_HELPERS = {
    "gear_involute": ("CreateInvoluteGear", "Create an involute gear using the freecad.gears workbench."),
    "gear_internal_involute": ("CreateInternalInvoluteGear", "Create an internal involute gear using the freecad.gears workbench."),
    "gear_worm": ("CreateWormGear", "Create a worm gear using the freecad.gears workbench."),
    "gear_bevel": ("CreateBevelGear", "Create a bevel gear using the freecad.gears workbench."),
}


def _candidate_mod_paths() -> list[str]:
    roots: list[Path] = []
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        roots.append(Path(xdg_data_home) / "FreeCAD" / "Mod")
    roots.append(Path.home() / ".local" / "share" / "FreeCAD" / "Mod")
    roots.append(Path("/usr/local/share/FreeCAD/Mod"))
    roots.append(Path("/usr/share/FreeCAD/Mod"))
    return [str(path) for path in roots]


def _ensure_import_path() -> None:
    for path in _candidate_mod_paths():
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _load_commands_module():
    _ensure_import_path()
    return importlib.import_module("freecad.gears.commands")


def _create_gear(command_name: str, App, Gui, Part, document, params: Dict[str, Any]):
    del Part
    commands = _load_commands_module()
    command_cls = getattr(commands, command_name)
    previous = App.ActiveDocument
    App.setActiveDocument(document.Name)
    if Gui is not None:
        gui_document = getattr(Gui, "getDocument", lambda name: None)(document.Name)
        if gui_document is not None:
            Gui.ActiveDocument = gui_document
    try:
        obj = command_cls.create()
    finally:
        if previous is not None:
            App.setActiveDocument(previous.Name)

    prop_map = {
        "num_teeth": params.get("num_teeth", params.get("teeth")),
        "module": params.get("module"),
        "height": params.get("height", params.get("thickness")),
        "pressure_angle": params.get("pressure_angle"),
        "helix_angle": params.get("helix_angle"),
        "double_helix": params.get("double_helix"),
        "backlash": params.get("backlash"),
        "clearance": params.get("clearance"),
        "shift": params.get("shift"),
        "head": params.get("head"),
        "simple": params.get("simple"),
        "inner_diameter": params.get("inner_diameter"),
        "outer_diameter": params.get("outer_diameter"),
        "pitch_diameter": params.get("pitch_diameter"),
        "axle_hole": params.get("axle_hole"),
        "axle_holesize": params.get("axle_holesize", params.get("bore_diameter")),
    }
    for key, value in prop_map.items():
        if value is not None and hasattr(obj, key):
            setattr(obj, key, value)
    if params.get("name"):
        obj.Label = params["name"]
    document.recompute()
    return obj


def get_helper_specs() -> list[HelperSpec]:
    specs = []
    try:
        commands = _load_commands_module()
    except Exception:
        commands = None
    for helper_name, (command_name, description) in GEAR_HELPERS.items():
        available = commands is not None and hasattr(commands, command_name)

        def _runner(App, Gui, Part, document, params, *, _command_name=command_name):
            return _create_gear(_command_name, App, Gui, Part, document, params)

        specs.append(
            HelperSpec(
                name=helper_name,
                source="freecad.gears",
                description=description,
                gui_required=False,
                available=available,
                runner=_runner,
            )
        )
    return specs
