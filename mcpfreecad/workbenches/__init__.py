"""Helper registry for built-in and optional FreeCAD workbenches."""

from __future__ import annotations

from mcpfreecad.workbenches.base import HelperSpec
from mcpfreecad.workbenches.builtin import get_helper_specs as builtin_helper_specs
from mcpfreecad.workbenches.fasteners import get_helper_specs as fastener_helper_specs
from mcpfreecad.workbenches.freecad_gears import get_helper_specs as gear_helper_specs


def discover_helper_specs() -> list[HelperSpec]:
    return builtin_helper_specs() + gear_helper_specs() + fastener_helper_specs()
