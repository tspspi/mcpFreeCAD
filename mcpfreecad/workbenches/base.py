"""Shared helper registry types for optional FreeCAD workbenches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass(frozen=True)
class HelperSpec:
    name: str
    source: str
    description: str
    gui_required: bool
    available: bool
    runner: Callable[[Any, Any, Any, Any, Dict[str, Any]], Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "description": self.description,
            "gui_required": self.gui_required,
            "available": self.available,
        }
