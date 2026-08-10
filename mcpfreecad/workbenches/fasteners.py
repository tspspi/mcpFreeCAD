"""Adapter for the optional Fasteners workbench."""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict

from mcpfreecad.workbenches.base import HelperSpec


FASTENER_HELPERS = {
    "fastener_screw": ("ISO4017", "Create a screw from the Fasteners workbench."),
    "fastener_washer": ("ISO7089", "Create a washer from the Fasteners workbench."),
    "fastener_nut": ("ISO4032", "Create a nut from the Fasteners workbench."),
    "fastener_threaded_rod": ("ThreadedRod", "Create a threaded rod from the Fasteners workbench."),
}


def _candidate_workbench_paths() -> list[str]:
    roots: list[Path] = []
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        roots.append(Path(xdg_data_home) / "FreeCAD" / "Mod")
    roots.append(Path.home() / ".local" / "share" / "FreeCAD" / "Mod")
    roots.append(Path("/usr/local/share/FreeCAD/Mod"))
    roots.append(Path("/usr/share/FreeCAD/Mod"))

    candidates: list[str] = []
    for root in roots:
        candidates.append(str(root / "fasteners"))
        candidates.append(str(root / "Fasteners"))
        candidates.append(str(root))
    return candidates


def _ensure_import_path() -> None:
    for path in _candidate_workbench_paths():
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _load_fasteners_module():
    _ensure_import_path()
    return importlib.import_module("FastenersCmd")


def _normalized_length_enum_candidates(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    candidates = [text]
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*mm\s*$", text, re.IGNORECASE)
    if match:
        number = match.group(1)
        candidates.append(number)
        if number.endswith(".0"):
            candidates.append(number[:-2])
    return candidates


def _normalized_diameter_enum_candidates(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    candidates = [text]
    match = re.match(r"^\s*M\s*([0-9]+(?:\.[0-9]+)?)\s*$", text, re.IGNORECASE)
    if match:
        number = match.group(1)
        candidates.append(f"M{number}")
        candidates.append(number)
    return candidates


def _set_property(obj, key: str, value: Any) -> None:
    type_id = obj.getTypeIdOfProperty(key)
    if type_id == "App::PropertyEnumeration":
        options = list(obj.getEnumerationsOfProperty(key))
        candidates = [str(value)]
        if key == "Length":
            candidates = _normalized_length_enum_candidates(value)
        elif key == "Diameter":
            candidates = _normalized_diameter_enum_candidates(value)

        for candidate in candidates:
            if candidate in options:
                setattr(obj, key, candidate)
                return

        if key == "Length" and "Custom" in options and hasattr(obj, "LengthCustom"):
            obj.Length = "Custom"
            obj.LengthCustom = value
            return
        if key == "Diameter" and "Custom" in options and hasattr(obj, "DiameterCustom"):
            obj.Diameter = "Custom"
            obj.DiameterCustom = value
            return

        raise ValueError(f"{value!r} is not a valid enumeration for {obj.Name}.{key}: {options}")

    setattr(obj, key, value)


def _create_fastener(default_type: str, App, Gui, Part, document, params: Dict[str, Any]):
    del Part
    if Gui is None:
        raise RuntimeError("Fasteners workbench requires a running FreeCAD GUI session")
    fasteners = _load_fasteners_module()
    obj = document.addObject("Part::FeaturePython", params.get("name", "Fastener"))
    fasteners.FSScrewObject(obj, params.get("type", default_type), None)
    if hasattr(fasteners, "FSViewProviderTree"):
        fasteners.FSViewProviderTree(obj.ViewObject)

    prop_map = {
        "Type": params.get("type", default_type),
        "Diameter": params.get("diameter"),
        "Length": params.get("length"),
        "LengthCustom": params.get("length_custom"),
        "Thread": params.get("thread"),
        "LeftHanded": params.get("left_handed"),
        "MatchOuter": params.get("match_outer"),
        "DiameterCustom": params.get("diameter_custom"),
        "PitchCustom": params.get("pitch_custom"),
        "offset": params.get("offset"),
        "Offset": params.get("offset"),
    }
    for key, value in prop_map.items():
        if value is not None and hasattr(obj, key):
            _set_property(obj, key, value)
    if params.get("name"):
        obj.Label = params["name"]
    document.recompute()
    return obj


def get_helper_specs() -> list[HelperSpec]:
    specs = []
    try:
        fasteners = _load_fasteners_module()
    except Exception:
        fasteners = None
    for helper_name, (default_type, description) in FASTENER_HELPERS.items():
        available = fasteners is not None and hasattr(fasteners, "FSScrewObject")

        def _runner(App, Gui, Part, document, params, *, _default_type=default_type):
            return _create_fastener(_default_type, App, Gui, Part, document, params)

        specs.append(
            HelperSpec(
                name=helper_name,
                source="fasteners",
                description=description,
                gui_required=True,
                available=available,
                runner=_runner,
            )
        )
    return specs
