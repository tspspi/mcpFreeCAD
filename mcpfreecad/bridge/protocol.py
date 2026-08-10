"""Shared payload helpers for the FreeCAD bridge."""

from __future__ import annotations

from typing import Any, Dict


def make_request(command: str, **params: Any) -> Dict[str, Any]:
    return {"command": command, "params": params}
