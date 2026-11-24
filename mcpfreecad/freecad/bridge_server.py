"""Bridge server intended to run inside FreeCAD's embedded Python."""

from __future__ import annotations

import json
import gc
import time
from functools import wraps
import math
import os
import sys
from contextlib import contextmanager, nullcontext
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from mcpfreecad.workbenches import discover_helper_specs

_bridge_state = globals().get("_bridge_state") or {
    "server": None,
    "thread": None,
    "token": None,
    "dispatcher": None,
}

_LIBRARY_SCHEMA_VERSION = 1


def _require_freecad_modules():
    import FreeCAD as App
    import Part
    import Sketcher

    try:
        import FreeCADGui as Gui
    except ImportError:
        Gui = None

    return App, Gui, Part, Sketcher




def _load_qtcore():
    try:
        from PySide import QtCore
    except ImportError:
        from PySide2 import QtCore
    return QtCore


class _BridgeDispatcher:
    """Execute FreeCAD bridge commands on the GUI/main thread."""

    def __init__(self):
        QtCore = _load_qtcore()

        class _Invoker(QtCore.QObject):
            bridge_signal = QtCore.Signal(object, object, object)

            def __init__(self):
                super().__init__()
                self.bridge_signal.connect(self._dispatch, QtCore.Qt.QueuedConnection)

            def _dispatch(self, command, params, result_queue):
                try:
                    result_queue.put(("ok", _execute_command_with_cad_view(command, params)))
                except Exception as exc:
                    result_queue.put(("error", exc))

        self._invoker = _Invoker()

    def call(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        self._invoker.bridge_signal.emit(command, params, result_queue)
        status, payload = result_queue.get()
        if status == "error":
            raise payload
        return payload


def _active_document(App, document_name: Optional[str] = None):
    if document_name:
        document = App.getDocument(document_name)
        setter = getattr(App, "setActiveDocument", None)
        if document is not None and callable(setter):
            try:
                setter(document.Name)
            except Exception:
                pass
            try:
                import FreeCADGui as Gui  # type: ignore
            except Exception:
                Gui = None
            _maybe_bind_gui_document(Gui, document.Name)
    else:
        document = App.ActiveDocument
    if document is None:
        raise RuntimeError("No active FreeCAD document")
    return document



def _maybe_bind_gui_document(Gui, document_name: str) -> None:
    if Gui is None:
        return
    get_document = getattr(Gui, "getDocument", None)
    if get_document is None:
        return
    gui_document = get_document(document_name)
    if gui_document is not None:
        Gui.ActiveDocument = gui_document



def _gui_document(Gui, document_name: str):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available")
    get_document = getattr(Gui, "getDocument", None)
    if get_document is None:
        raise RuntimeError("FreeCAD GUI document access is not available")
    gui_document = get_document(document_name)
    if gui_document is None:
        raise RuntimeError(f"GUI document '{document_name}' is not available")
    return gui_document



def _active_view_for_document(Gui, document_name: str):
    _maybe_bind_gui_document(Gui, document_name)
    gui_document = _gui_document(Gui, document_name)
    active_view = getattr(gui_document, "ActiveView", None)
    if active_view is None:
        raise RuntimeError(f"Active 3D view for document '{document_name}' is not available")
    return active_view



def _vector(App, values):
    values = values or [0.0, 0.0, 1.0]
    return App.Vector(float(values[0]), float(values[1]), float(values[2]))



def _placement(App, payload):
    if not payload:
        return None
    base = payload.get("base", [0.0, 0.0, 0.0])
    axis = payload.get("axis", [0.0, 0.0, 1.0])
    angle = float(payload.get("angle_degrees", 0.0))
    return App.Placement(
        App.Vector(float(base[0]), float(base[1]), float(base[2])),
        App.Rotation(_vector(App, axis), angle),
    )



def _object_summary(obj) -> Dict[str, Any]:
    return {
        "name": obj.Name,
        "label": obj.Label,
        "type_id": getattr(obj, "TypeId", None),
    }



def _vector_payload(vector) -> list[float]:
    return [float(vector.x), float(vector.y), float(vector.z)]



def _axis_payload(axis) -> list[float]:
    return [float(axis.x), float(axis.y), float(axis.z)]



def _sketch_status(sketch) -> Dict[str, Any]:
    solve_status = None
    solve_error = None
    try:
        solve_status = sketch.solve()
    except Exception as exc:
        solve_error = str(exc)

    shape_is_null = True
    wire_count = 0
    closed_wires: list[bool] = []
    if hasattr(sketch, "Shape"):
        try:
            shape_is_null = sketch.Shape.isNull()
            if not shape_is_null:
                wires = list(sketch.Shape.Wires)
                wire_count = len(wires)
                closed_wires = [bool(wire.isClosed()) for wire in wires]
        except Exception as exc:
            solve_error = solve_error or str(exc)

    return {
        "name": sketch.Name,
        "geometry_count": int(getattr(sketch, "GeometryCount", 0)),
        "constraint_count": int(getattr(sketch, "ConstraintCount", 0)),
        "fully_constrained": bool(getattr(sketch, "FullyConstrained", False)),
        "dof": getattr(sketch, "DoF", None),
        "status_string": sketch.getStatusString() if hasattr(sketch, "getStatusString") else None,
        "solve_status": solve_status,
        "solve_error": solve_error,
        "shape_is_null": shape_is_null,
        "wire_count": wire_count,
        "closed_wires": closed_wires,
    }



def _serialize_sketch_geometry(geometry) -> Dict[str, Any]:
    geom_type = type(geometry).__name__
    result: Dict[str, Any] = {"type": geom_type}
    if hasattr(geometry, "StartPoint") and hasattr(geometry, "EndPoint"):
        result["start"] = [geometry.StartPoint.x, geometry.StartPoint.y, geometry.StartPoint.z]
        result["end"] = [geometry.EndPoint.x, geometry.EndPoint.y, geometry.EndPoint.z]
    if hasattr(geometry, "Center"):
        result["center"] = [geometry.Center.x, geometry.Center.y, geometry.Center.z]
    if hasattr(geometry, "Radius"):
        result["radius"] = float(geometry.Radius)
    if hasattr(geometry, "Degree"):
        result["degree"] = int(geometry.Degree)
    if hasattr(geometry, "getPoles"):
        try:
            poles = geometry.getPoles()
            result["poles"] = [[pole.x, pole.y, pole.z] for pole in poles]
        except Exception:
            pass
    if hasattr(geometry, "isClosed"):
        try:
            result["closed"] = bool(geometry.isClosed())
        except Exception:
            pass
    return result



def _serialize_sketch_constraint(constraint) -> Dict[str, Any]:
    result = {
        "type": getattr(constraint, "Type", None),
        "name": getattr(constraint, "Name", ""),
        "first": getattr(constraint, "First", None),
        "first_pos": getattr(constraint, "FirstPos", None),
        "second": getattr(constraint, "Second", None),
        "second_pos": getattr(constraint, "SecondPos", None),
        "third": getattr(constraint, "Third", None),
        "third_pos": getattr(constraint, "ThirdPos", None),
    }
    if hasattr(constraint, "Value"):
        try:
            result["value"] = float(constraint.Value)
        except Exception:
            result["value"] = _serialize_property_value(constraint.Value)
    return result



def _sketch_details(sketch) -> Dict[str, Any]:
    details = _sketch_status(sketch)
    details["geometry"] = [
        {"index": index, **_serialize_sketch_geometry(sketch.Geometry[index])}
        for index in range(int(getattr(sketch, "GeometryCount", 0)))
    ]
    details["constraints"] = [
        {"index": index, **_serialize_sketch_constraint(sketch.Constraints[index])}
        for index in range(int(getattr(sketch, "ConstraintCount", 0)))
    ]
    return details



def _ensure_sketch_profile_ready(sketch, operation: str) -> None:
    status = _sketch_status(sketch)
    if status["solve_status"] not in (None, 0):
        raise RuntimeError(f"{operation}: sketch '{sketch.Name}' solver status={status['solve_status']}")
    if status["shape_is_null"]:
        raise RuntimeError(f"{operation}: sketch '{sketch.Name}' has null shape")
    if status["wire_count"] < 1:
        raise RuntimeError(f"{operation}: sketch '{sketch.Name}' has no wires")
    if not any(status["closed_wires"]):
        raise RuntimeError(f"{operation}: sketch '{sketch.Name}' has no closed wire")



def _sketch_by_name(App, params: Dict[str, Any]):
    document = _active_document(App, params.get("document_name"))
    sketch = document.getObject(params["sketch_name"])
    if sketch is None:
        raise RuntimeError(f"Sketch '{params['sketch_name']}' not found")
    return document, sketch



def _object_by_name(App, params: Dict[str, Any]):
    document = _active_document(App, params.get("document_name"))
    obj = document.getObject(params["object_name"])
    if obj is None:
        raise RuntimeError(f"Object '{params['object_name']}' not found")
    return document, obj



def _open_document_for_inspection(App, Gui, path: str):
    normalized_path = str(Path(path).expanduser())
    existing = None
    for document in App.listDocuments().values():
        if getattr(document, "FileName", None) == normalized_path:
            existing = document
            break
    if existing is not None:
        return existing, False, normalized_path

    previous_active = App.ActiveDocument.Name if App.ActiveDocument else None
    document = App.openDocument(normalized_path)
    if document is None:
        raise RuntimeError(f"Failed to open FreeCAD document at '{normalized_path}'")
    if previous_active is not None:
        App.setActiveDocument(previous_active)
        _maybe_bind_gui_document(Gui, previous_active)
    return document, True, normalized_path



def _close_temp_document(App, opened_document, should_close: bool):
    if should_close and opened_document is not None:
        App.closeDocument(opened_document.Name)



def _resolve_constraint_index(sketch, constraint_index: Optional[int] = None, constraint_name: Optional[str] = None) -> int:
    count = int(getattr(sketch, "ConstraintCount", 0))
    if constraint_name:
        for index in range(count):
            if getattr(sketch.Constraints[index], "Name", "") == constraint_name:
                return index
        raise RuntimeError(f"Constraint named '{constraint_name}' not found in sketch '{sketch.Name}'")
    if constraint_index is None:
        raise RuntimeError("Either constraint_index or constraint_name is required")
    resolved = int(constraint_index)
    if resolved < 0 or resolved >= count:
        raise RuntimeError(f"Constraint index {resolved} is out of range for sketch '{sketch.Name}'")
    return resolved



def _ensure_constraint_name_available(sketch, constraint_name: Optional[str]) -> None:
    if not constraint_name:
        return
    count = int(getattr(sketch, "ConstraintCount", 0))
    for index in range(count):
        if getattr(sketch.Constraints[index], "Name", "") == constraint_name:
            raise RuntimeError(f"Constraint named '{constraint_name}' already exists in sketch '{sketch.Name}'")



def _add_named_constraint(sketch, Sketcher, constraint_type: str, args: list[Any], constraint_name: Optional[str] = None) -> int:
    _ensure_constraint_name_available(sketch, constraint_name)
    constraint_index = int(sketch.addConstraint(Sketcher.Constraint(constraint_type, *args)))
    if constraint_name:
        sketch.renameConstraint(constraint_index, str(constraint_name))
    return constraint_index



def _normalize_constraint_expression(document, expression: str) -> str:
    if not isinstance(expression, str):
        return expression
    if expression.endswith(".Value"):
        return expression
    match = re.fullmatch(r"(?:<<)?([A-Za-z_]\w*)(?:>>)?\.([A-Za-z_]\w*)", expression.strip())
    if match is None:
        return expression
    object_name, alias_name = match.groups()
    obj = document.getObject(object_name)
    if obj is None or getattr(obj, "TypeId", None) != "Spreadsheet::Sheet":
        return expression
    if not hasattr(obj, "getCellFromAlias") or not hasattr(obj, "getContents"):
        return f"<<{object_name}>>.{alias_name}"
    try:
        cell_name = obj.getCellFromAlias(alias_name)
        raw_contents = obj.getContents(cell_name)
    except Exception:
        return f"<<{object_name}>>.{alias_name}"
    normalized = f"<<{object_name}>>.{alias_name}"
    content_text = str(raw_contents).lstrip("=").strip()
    if any(ch.isalpha() for ch in content_text):
        return normalized + ".Value"
    return normalized



def _resolve_body(document, body_name: Optional[str] = None):
    if body_name:
        body = document.getObject(body_name)
    else:
        body = next((obj for obj in document.Objects if getattr(obj, "TypeId", None) == "PartDesign::Body"), None)
    if body is None:
        raise RuntimeError("No PartDesign body found")
    if getattr(body, "TypeId", None) != "PartDesign::Body":
        raise RuntimeError(f"Object '{body.Name}' is not a PartDesign body")
    return body



def _set_active_body(App, Gui, document, body) -> None:
    App.setActiveDocument(document.Name)
    _maybe_bind_gui_document(Gui, document.Name)
    if Gui is not None and getattr(Gui, "ActiveDocument", None) is not None:
        active_view = getattr(Gui.ActiveDocument, "ActiveView", None)
        if active_view is not None and hasattr(active_view, "setActiveObject"):
            active_view.setActiveObject("pdbody", body)



def _body_origin_feature(body, plane_name: str):
    candidates = list(getattr(body.Origin, "OriginFeatures", []))
    for feature in candidates:
        if feature.Name == plane_name or getattr(feature, "Role", None) == plane_name:
            return feature
    raise RuntimeError(f"Body origin plane '{plane_name}' not found")



def _serialize_property_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialize_property_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_property_value(item) for key, item in value.items()}
    if hasattr(value, "Value"):
        try:
            return str(value)
        except Exception:
            return repr(value)
    if hasattr(value, "Name") and hasattr(value, "Label"):
        return {
            "name": getattr(value, "Name", None),
            "label": getattr(value, "Label", None),
            "type_id": getattr(value, "TypeId", None),
        }
    return repr(value)



def _object_properties(obj, include_properties: bool, property_names: Optional[list[str]]) -> Dict[str, Any]:
    if not include_properties:
        return {}
    names = property_names or list(getattr(obj, "PropertiesList", []))
    result = {}
    for name in names:
        if not (hasattr(type(obj), name) or name in getattr(obj, "PropertiesList", [])):
            continue
        try:
            result[name] = _serialize_property_value(getattr(obj, name))
        except Exception as exc:
            result[name] = f"<unreadable: {exc}>"
    return result



def _document_tree(document, include_properties: bool = False, property_names: Optional[list[str]] = None, max_depth: int = 4, root_name: Optional[str] = None) -> Dict[str, Any]:
    objects = list(document.Objects)
    object_map = {obj.Name: obj for obj in objects}

    children_by_name: dict[str, list[Any]] = {name: [] for name in object_map}
    parents_by_name: dict[str, set[str]] = {name: set() for name in object_map}

    for obj in objects:
        child_candidates = []
        out_list = getattr(obj, "OutList", []) or []
        child_candidates.extend(child for child in out_list if getattr(child, "Document", None) == document)
        group = getattr(obj, "Group", None)
        if group:
            child_candidates.extend(child for child in group if getattr(child, "Document", None) == document)

        unique_children = []
        seen_children = set()
        for child in child_candidates:
            if child.Name in seen_children:
                continue
            seen_children.add(child.Name)
            unique_children.append(child)

        children_by_name[obj.Name] = unique_children
        for child in unique_children:
            parents_by_name.setdefault(child.Name, set()).add(obj.Name)

    def build_node(obj, depth: int, path: set[str]) -> Dict[str, Any]:
        node = _object_summary(obj)
        node["properties"] = _object_properties(obj, include_properties, property_names)
        if depth >= max_depth:
            node["children"] = []
            node["truncated"] = True
            return node
        if obj.Name in path:
            node["children"] = []
            node["cycle"] = True
            return node
        next_path = set(path)
        next_path.add(obj.Name)
        node["children"] = [build_node(child, depth + 1, next_path) for child in children_by_name.get(obj.Name, [])]
        return node

    if root_name is not None:
        root = object_map.get(root_name)
        if root is None:
            raise RuntimeError(f"Object '{root_name}' not found in document '{document.Name}'")
        roots = [root]
    else:
        roots = [obj for obj in objects if not parents_by_name.get(obj.Name)]
        if not roots:
            roots = objects

    return {
        "document": document.Name,
        "object_count": len(objects),
        "roots": [build_node(root, 0, set()) for root in roots],
    }



def _optional_workbench_support() -> Dict[str, Dict[str, Any]]:
    support: Dict[str, Dict[str, Any]] = {
        "a2plus": {
            "available": False,
            "gui_required": True,
            "module_dir": None,
            "importable": False,
        }
    }
    roots = []
    xdg_data_home = __import__("os").environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        roots.append(Path(xdg_data_home))
    roots.append(Path.home() / ".local" / "share")
    for root in roots:
        module_dir = root / "FreeCAD" / "Mod" / "A2plus"
        if module_dir.exists():
            support["a2plus"]["available"] = True
            support["a2plus"]["module_dir"] = str(module_dir)
            break
    return support


def _ensure_a2plus_support():
    support = _optional_workbench_support()["a2plus"]
    if not support["available"]:
        raise RuntimeError("A2plus workbench is not available in this FreeCAD environment")
    module_dir = support["module_dir"]
    import sys

    if module_dir and module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    try:
        import a2plib  # noqa: F401
        import a2p_importpart  # noqa: F401
    except Exception as exc:
        raise RuntimeError(f"A2plus workbench is installed but unavailable in the current GUI session: {exc}") from exc
    _patch_a2plus_solver_dialog_suppression()
    return support



def _status_payload() -> Dict[str, Any]:
    App, Gui, Part, Sketcher = _require_freecad_modules()
    del Part, Sketcher
    helpers = [spec.to_dict() for spec in discover_helper_specs()]
    helpers.sort(key=lambda entry: entry["name"])
    return {
        "running": True,
        "freecad_version": App.Version(),
        "gui_up": bool(getattr(App, "GuiUp", False)),
        "gui_available": Gui is not None,
        "active_document": _current_active_document_name(App),
        "helpers": helpers,
        "optional_workbenches": _optional_workbench_support(),
    }


def _add_line_segment(App, Part, sketch, start, end) -> int:
    return sketch.addGeometry(
        Part.LineSegment(
            App.Vector(float(start[0]), float(start[1]), float(start[2] if len(start) > 2 else 0.0)),
            App.Vector(float(end[0]), float(end[1]), float(end[2] if len(end) > 2 else 0.0)),
        ),
        False,
    )



def _ensure_library_entry(library_root: str, entry_id: str, overwrite: bool) -> Path:
    entry_dir = Path(library_root).expanduser() / entry_id
    if entry_dir.exists() and not overwrite:
        raise RuntimeError(f"Library entry '{entry_id}' already exists")
    entry_dir.mkdir(parents=True, exist_ok=True)
    return entry_dir



def _library_metadata_path(entry_dir: Path) -> Path:
    return entry_dir / "metadata.json"



def _library_shape_path(entry_dir: Path) -> Path:
    return entry_dir / "shape.brep"



def _build_library_metadata(entry_id: str, document_name: str, obj, metadata: Dict[str, Any]) -> Dict[str, Any]:
    user_metadata = dict(metadata or {})
    user_metadata.pop("entry_id", None)
    user_metadata.pop("schema_version", None)
    user_metadata.pop("object", None)
    user_metadata.pop("storage", None)
    user_metadata.pop("source_document", None)
    return {
        "schema_version": _LIBRARY_SCHEMA_VERSION,
        "entry_id": entry_id,
        "source_document": document_name,
        "object": {
            "name": obj.Name,
            "label": obj.Label,
            "type_id": getattr(obj, "TypeId", None),
        },
        "storage": {
            "format": "brep",
            "shape_file": "shape.brep",
        },
        "user_metadata": user_metadata,
    }



def _validate_library_metadata(entry_id: str, metadata: Any) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        raise RuntimeError(f"Library entry '{entry_id}' metadata must be a JSON object")
    schema_version = metadata.get("schema_version")
    if schema_version != _LIBRARY_SCHEMA_VERSION:
        raise RuntimeError(
            f"Library entry '{entry_id}' uses unsupported schema_version={schema_version!r}; expected {_LIBRARY_SCHEMA_VERSION}"
        )
    if metadata.get("entry_id") != entry_id:
        raise RuntimeError(f"Library entry '{entry_id}' metadata has mismatched entry_id={metadata.get('entry_id')!r}")
    obj_meta = metadata.get("object")
    if not isinstance(obj_meta, dict):
        raise RuntimeError(f"Library entry '{entry_id}' metadata is missing object details")
    storage_meta = metadata.get("storage")
    if not isinstance(storage_meta, dict):
        raise RuntimeError(f"Library entry '{entry_id}' metadata is missing storage details")
    if storage_meta.get("format") != "brep" or storage_meta.get("shape_file") != "shape.brep":
        raise RuntimeError(f"Library entry '{entry_id}' metadata declares unsupported storage format")
    user_meta = metadata.get("user_metadata", {})
    if not isinstance(user_meta, dict):
        raise RuntimeError(f"Library entry '{entry_id}' user_metadata must be a JSON object")
    return metadata



def _read_library_metadata(entry_dir: Path, entry_id: str) -> Dict[str, Any]:
    metadata_path = _library_metadata_path(entry_dir)
    if not metadata_path.exists():
        raise RuntimeError(f"Library entry '{entry_id}' is missing metadata.json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Library entry '{entry_id}' metadata.json is invalid JSON: {exc}") from exc
    return _validate_library_metadata(entry_id, metadata)



def _helper_registry():
    return {spec.name: spec for spec in discover_helper_specs()}



def _parametric_primitive_spec(kind: str):
    specs = {
        "box": ("Part::Box", {"length": "Length", "width": "Width", "height": "Height"}),
        "cylinder": ("Part::Cylinder", {"radius": "Radius", "height": "Height", "angle": "Angle"}),
        "sphere": ("Part::Sphere", {"radius": "Radius", "angle1": "Angle1", "angle2": "Angle2", "angle3": "Angle3"}),
        "cone": ("Part::Cone", {"radius1": "Radius1", "radius2": "Radius2", "height": "Height", "angle": "Angle"}),
        "torus": ("Part::Torus", {"radius1": "Radius1", "radius2": "Radius2", "angle1": "Angle1", "angle2": "Angle2", "angle3": "Angle3"}),
    }
    spec = specs.get(kind.lower())
    if spec is None:
        supported = ", ".join(sorted(specs))
        raise RuntimeError(f"Unsupported parametric primitive kind '{kind}'. Supported: {supported}")
    return spec



def _view_method_name(orientation: str) -> str:
    methods = {
        "top": "viewTop",
        "bottom": "viewBottom",
        "front": "viewFront",
        "back": "viewRear",
        "rear": "viewRear",
        "left": "viewLeft",
        "right": "viewRight",
        "isometric": "viewAxonometric",
        "axonometric": "viewAxonometric",
    }
    method_name = methods.get(str(orientation).strip().lower())
    if method_name is None:
        supported = ", ".join(sorted(methods))
        raise RuntimeError(f"Unsupported view orientation '{orientation}'. Supported: {supported}")
    return method_name



def _set_view_orientation(active_view, orientation: str) -> str:
    method_name = _view_method_name(orientation)
    view_method = getattr(active_view, method_name, None)
    if view_method is None:
        raise RuntimeError(f"Active view does not support orientation method '{method_name}'")
    get_animation = getattr(active_view, "isAnimationEnabled", None)
    set_animation = getattr(active_view, "setAnimationEnabled", None)
    if callable(get_animation) and callable(set_animation):
        animated = bool(get_animation())
        try:
            # saveImage can otherwise capture the first frame of the transition.
            set_animation(False)
            view_method()
        finally:
            set_animation(animated)
    else:
        view_method()
    return method_name



def _set_objects_visibility(document, object_names: list[str], visible: bool) -> Dict[str, Any]:
    updated = []
    missing = []
    for object_name in object_names:
        obj = document.getObject(object_name)
        if obj is None:
            missing.append(object_name)
            continue
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None or not hasattr(view_object, "Visibility"):
            missing.append(object_name)
            continue
        view_object.Visibility = bool(visible)
        updated.append(object_name)
    return {"updated": updated, "missing": missing, "visible": bool(visible)}



def _support_link_for_face(document, object_name: str, face_name: str):
    obj = document.getObject(object_name)
    if obj is None:
        raise RuntimeError(f"Object '{object_name}' not found in document '{document.Name}'")
    if not str(face_name).startswith("Face"):
        raise RuntimeError(f"Unsupported face selector '{face_name}'. Expected values like 'Face1'")
    return obj, (obj, [str(face_name)])



def _serialize_shape_topology(shape, *, include_vertices: bool = True, summary_only: bool = False) -> Dict[str, Any]:
    if summary_only:
        solid_count = len(shape.Solids)
        return {
            "solid_count": solid_count,
            "single_solid": solid_count == 1,
            "shape_valid": bool(shape.isValid()),
            "volume": float(shape.Volume),
            "face_count": len(shape.Faces),
            "edge_count": len(shape.Edges),
            "vertex_count": len(shape.Vertexes),
        }
    faces = []
    for index, face in enumerate(getattr(shape, "Faces", []) or [], start=1):
        entry: Dict[str, Any] = {
            "name": f"Face{index}",
            "surface_type": type(getattr(face, "Surface", None)).__name__ if getattr(face, "Surface", None) is not None else None,
            "area": float(getattr(face, "Area", 0.0)),
            "center_of_mass": _vector_payload(face.CenterOfMass),
        }
        surface = getattr(face, "Surface", None)
        if surface is not None and hasattr(surface, "Axis"):
            entry["axis"] = _axis_payload(surface.Axis)
        if surface is not None and hasattr(surface, "Position"):
            entry["position"] = _vector_payload(surface.Position)
        if surface is not None and hasattr(surface, "Radius"):
            entry["radius"] = float(surface.Radius)
        faces.append(entry)

    edges = []
    for index, edge in enumerate(getattr(shape, "Edges", []) or [], start=1):
        curve = getattr(edge, "Curve", None)
        entry: Dict[str, Any] = {
            "name": f"Edge{index}",
            "curve_type": type(curve).__name__ if curve is not None else None,
            "length": float(getattr(edge, "Length", 0.0)),
            "closed": bool(edge.isClosed()) if hasattr(edge, "isClosed") else False,
            "center_of_mass": _vector_payload(edge.CenterOfMass),
        }
        if curve is not None and hasattr(curve, "Axis"):
            entry["axis"] = _axis_payload(curve.Axis)
        if curve is not None and hasattr(curve, "Location"):
            entry["location"] = _vector_payload(curve.Location)
        if curve is not None and hasattr(curve, "Radius"):
            entry["radius"] = float(curve.Radius)
        edges.append(entry)

    vertices = []
    if include_vertices:
        for index, vertex in enumerate(getattr(shape, "Vertexes", []) or [], start=1):
            vertices.append({"name": f"Vertex{index}", "point": _vector_payload(vertex.Point)})

    return {
        "face_count": len(faces),
        "edge_count": len(edges),
        "vertex_count": len(vertices) if include_vertices else len(getattr(shape, "Vertexes", []) or []),
        "faces": faces,
        "edges": edges,
        "vertices": vertices,
    }



def _a2plus_constraint_spec(kind: str):
    normalized = str(kind).strip()
    specs = {
        "pointIdentity": "PointIdentityConstraint",
        "pointOnLine": "PointOnLineConstraint",
        "pointOnPlane": "PointOnPlaneConstraint",
        "sphereCenterIdent": "SphericalConstraint",
        "circularEdge": "CircularEdgeConstraint",
        "axial": "AxialConstraint",
        "axisParallel": "AxisParallelConstraint",
        "axisPlaneParallel": "AxisPlaneParallelConstraint",
        "axisPlaneAngle": "AxisPlaneAngleConstraint",
        "axisPlaneNormal": "AxisPlaneNormalConstraint",
        "planesParallel": "PlanesParallelConstraint",
        "plane": "PlaneConstraint",
        "angledPlanes": "AngledPlanesConstraint",
        "CenterOfMass": "CenterOfMassConstraint",
    }
    class_name = specs.get(normalized)
    if class_name is None:
        supported = ", ".join(sorted(specs))
        raise RuntimeError(f"Unsupported A2plus constraint kind '{kind}'. Supported: {supported}")
    return normalized, class_name



def _serialize_a2plus_constraint(obj) -> Dict[str, Any]:
    payload = _object_summary(obj) | {
        "constraint_type": getattr(obj, "Type", None),
        "object1": getattr(obj, "Object1", None),
        "subelement1": getattr(obj, "SubElement1", None),
        "object2": getattr(obj, "Object2", None),
        "subelement2": getattr(obj, "SubElement2", None),
        "suppressed": getattr(obj, "Suppressed", None),
    }
    if hasattr(obj, "directionConstraint"):
        payload["direction_constraint"] = str(obj.directionConstraint)
    if hasattr(obj, "offset"):
        payload["offset"] = str(obj.offset)
    if hasattr(obj, "angle"):
        payload["angle"] = str(obj.angle)
    if hasattr(obj, "lockRotation"):
        payload["lock_rotation"] = bool(obj.lockRotation)
    return payload



def _a2plus_constraint_by_name(document, constraint_name: str):
    constraint = document.getObject(constraint_name)
    if constraint is None:
        raise RuntimeError(f"A2plus constraint '{constraint_name}' not found")
    if "ConstraintInfo" not in getattr(constraint, "Content", ""):
        raise RuntimeError(f"Object '{constraint_name}' is not an A2plus constraint")
    return constraint



def _execute_command_impl(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    App, Gui, Part, Sketcher = _require_freecad_modules()
    from . import techdraw, techdraw_dimensions
    if command in techdraw.COMMANDS or command in techdraw_dimensions.COMMANDS:
        document = None if command in ('list_techdraw_presets','list_techdraw_templates') else _active_document(App,params.get('document_name'))
        return techdraw.execute(command,params,App,Gui,document)
    handler = _CAD_COMMAND_HANDLERS.get(command)
    if handler is None:
        raise RuntimeError(f"Unsupported command '{command}'")
    return handler(command,params,App,Gui,Part,Sketcher)


def _execute_command(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    App, Gui, Part, Sketcher = _require_freecad_modules()
    del Part, Sketcher

    dispatcher = _bridge_state.get("dispatcher")
    if dispatcher is not None and bool(getattr(App, "GuiUp", False)) and _command_requires_gui_dispatch(command, params, Gui):
        return dispatcher.call(command, params)
    return _execute_command_with_cad_view(command, params)


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "FreeCADBridge/0.1"

    def _json_response(self, status: int, payload: Dict[str, Any]):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = getattr(self.server, "bridge_token", None)
        if not token:
            return True
        header = self.headers.get("Authorization")
        return header == f"Bearer {token}"

    def do_GET(self):
        if self.path != "/status":
            self._json_response(404, {"ok": False, "error": "Not found"})
            return
        self._json_response(200, _status_payload())

    def do_POST(self):
        if self.path != "/rpc":
            self._json_response(404, {"ok": False, "error": "Not found"})
            return
        if not self._authorized():
            self._json_response(401, {"ok": False, "error": "Unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = _execute_command(payload["command"], payload.get("params") or {})
        except Exception as exc:
            self._json_response(500, {"ok": False, "error": str(exc)})
            return
        self._json_response(200, {"ok": True, "result": result})

    def log_message(self, format: str, *args):
        return


def _serialized_bridge_lifecycle(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with _bridge_state.setdefault("lifecycle_lock", threading.RLock()):
            return function(*args, **kwargs)
    return locked


@_serialized_bridge_lifecycle
def start_bridge_server(
    host: str = "127.0.0.1",
    port: int = 48111,
    token: Optional[str] = None,
    *,
    create_dispatcher: bool = True,
    dispatcher_override=None,
) -> Dict[str, Any]:
    _reconcile_bridge_state_from_runtime()
    if _bridge_state["server"] is not None:
        if _bridge_state.get("host") in (None, ""):
            _bridge_state["host"] = str(host)
        if _bridge_state.get("port") is None:
            _bridge_state["port"] = int(port)
        if token is not None and _bridge_state.get("token") in (None, ""):
            _bridge_state["token"] = token
        if dispatcher_override is not None:
            _bridge_state["dispatcher"] = dispatcher_override
        return {"running": True, "host": _bridge_runtime_host(), "port": _bridge_runtime_port()}
    if dispatcher_override is not None:
        _bridge_state["dispatcher"] = dispatcher_override
    elif create_dispatcher:
        try:
            App, Gui, Part, Sketcher = _require_freecad_modules()
            del Part, Sketcher
            if bool(getattr(App, "GuiUp", False)) and Gui is not None:
                _bridge_state["dispatcher"] = _BridgeDispatcher()
            else:
                _bridge_state["dispatcher"] = None
        except Exception:
            _bridge_state["dispatcher"] = None
    server = _BridgeHTTPServer((host, port), _BridgeHandler)
    server.bridge_token = token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _bridge_state["server"] = server
    _bridge_state["thread"] = thread
    _bridge_state["host"] = str(server.server_address[0])
    _bridge_state["port"] = int(server.server_address[1])
    _bridge_state["token"] = token
    _clear_restart_error()
    return {"running": True, "host": _bridge_runtime_host(), "port": _bridge_runtime_port()}



@_serialized_bridge_lifecycle
def stop_bridge_server(*, preserve_dispatcher: bool = False) -> Dict[str, Any]:
    _reconcile_bridge_state_from_runtime()
    server = _bridge_state["server"]
    if server is None:
        return {"running": False}
    server.shutdown()
    server.server_close()
    _bridge_state["server"] = None
    _bridge_state["thread"] = None
    _bridge_state["token"] = None
    if not preserve_dispatcher:
        _bridge_state["dispatcher"] = None
    return {"running": False}



def _cmd_add_sketch_constraint(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    constraint_index = sketch.addConstraint(
        Sketcher.Constraint(params["constraint_type"], *(params.get("args") or []))
    )
    constraint_name = params.get("constraint_name")
    if constraint_name:
        sketch.renameConstraint(int(constraint_index), str(constraint_name))
    document.recompute()
    return {
        "sketch": sketch.Name,
        "constraint_index": int(constraint_index),
        "status": _sketch_status(sketch),
        "constraint": {"index": int(constraint_index), **_serialize_sketch_constraint(sketch.Constraints[constraint_index])},
    }



def _cmd_attach_sketch_to_face(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    support_object, support_link = _support_link_for_face(document, params["support_object_name"], params["face_name"])
    if "Support" in sketch.PropertiesList:
        sketch.Support = support_link
    elif "AttachmentSupport" in sketch.PropertiesList:
        sketch.AttachmentSupport = [(support_object, (str(params["face_name"]),))]
    else:
        raise RuntimeError(f"Sketch '{sketch.Name}' does not expose a support attachment property")
    if params.get("map_mode") is not None and hasattr(sketch, "MapMode"):
        sketch.MapMode = params["map_mode"]
    document.recompute()
    return {
        "sketch": sketch.Name,
        "support_object": support_object.Name,
        "face_name": str(params["face_name"]),
        "map_mode": getattr(sketch, "MapMode", None),
    }



def _cmd_capture_snapshot(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot capture snapshots")
    document = _active_document(App, params.get("document_name"))
    active_view = _active_view_for_document(Gui, document.Name)
    orientation = params.get("orientation")
    if orientation:
        _set_view_orientation(active_view, orientation)
    if params.get("fit_view", True):
        active_view.fitAll()
    target_path = _snapshot_output_path(
        params.get("filename"), str(_snapshot_library_root(params)), f"{document.Name}.png"
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    active_view.saveImage(str(target_path), int(params.get("width", 1600)), int(params.get("height", 1200)), "White")
    return {"path": str(target_path), "orientation": orientation, "fit_view": bool(params.get("fit_view", True))}



def _cmd_catalog_file_objects(command, params, App, Gui, Part, Sketcher):
    document = None
    should_close = False
    try:
        document, should_close, normalized_path = _open_document_for_inspection(App, Gui, params["path"])
        include_topology = bool(params.get("include_topology", False))
        include_vertices = bool(params.get("include_vertices", True))
        include_properties = bool(params.get("include_properties", False))
        property_names = params.get("property_names")
        objects = []
        for obj in document.Objects:
            entry = _object_summary(obj)
            entry["properties"] = _object_properties(obj, include_properties, property_names)
            shape = getattr(obj, "Shape", None)
            if include_topology and shape is not None and not shape.isNull():
                entry["topology"] = _serialize_shape_topology(shape, include_vertices=include_vertices)
            objects.append(entry)
        objects.sort(key=lambda entry: entry["name"])
        return {
            "document": document.Name,
            "path": normalized_path,
            "object_count": len(objects),
            "objects": objects,
        }
    finally:
        _close_temp_document(App, document, should_close)



def _cmd_close_document(command, params, App, Gui, Part, Sketcher):
    name = str(params.get("name") or params.get("document_name") or "").strip()
    if not name:
        raise RuntimeError("close_document requires name or document_name")
    document = App.getDocument(name)
    if document is None:
        raise RuntimeError(f"Document '{name}' not found")
    App.closeDocument(name)
    remaining_docs = list(App.listDocuments().keys())
    active = _current_active_document_name(App)
    if active is not None:
        _maybe_bind_gui_document(Gui, active)
    return {
        "closed_document": name,
        "documents": remaining_docs,
        "active_document": active,
    }



def _cmd_create_a2plus_constraint(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot create A2plus assembly constraints")
    _ensure_a2plus_support()
    import a2plib
    import a2p_constraints
    import a2p_solversystem

    document = _active_document(App, params.get("document_name"))
    object1 = document.getObject(params["object1_name"])
    object2 = document.getObject(params["object2_name"])
    if object1 is None:
        raise RuntimeError(f"Assembly object '{params['object1_name']}' not found")
    if object2 is None:
        raise RuntimeError(f"Assembly object '{params['object2_name']}' not found")
    constraint_kind, class_name = _a2plus_constraint_spec(params["constraint_kind"])
    constraint_class = getattr(a2p_constraints, class_name)
    selection = [
        a2plib.SelectionExObject(document, object1, str(params["subelement1"])),
        a2plib.SelectionExObject(document, object2, str(params["subelement2"])),
    ]
    if not constraint_class.isValidSelection(selection):
        raise RuntimeError(
            f"A2plus selection is invalid for constraint '{constraint_kind}': "
            f"{object1.Name}.{params['subelement1']} -> {object2.Name}.{params['subelement2']}"
        )
    previous_active = App.ActiveDocument.Name if App.ActiveDocument else None
    App.setActiveDocument(document.Name)
    _maybe_bind_gui_document(Gui, document.Name)
    constraint = constraint_class(selection).constraintObject

    requested_name = params.get("name")
    if requested_name:
        constraint.Label = str(requested_name)
    if params.get("direction_constraint") is not None and hasattr(constraint, "directionConstraint"):
        constraint.directionConstraint = str(params["direction_constraint"])
    if params.get("offset") is not None and hasattr(constraint, "offset"):
        constraint.offset = float(params["offset"])
    if params.get("angle") is not None and hasattr(constraint, "angle"):
        constraint.angle = float(params["angle"])
    if params.get("lock_rotation") is not None and hasattr(constraint, "lockRotation"):
        constraint.lockRotation = bool(params["lock_rotation"])
    if params.get("suppressed") is not None and hasattr(constraint, "Suppressed"):
        constraint.Suppressed = bool(params["suppressed"])

    document.recompute()
    solved = None
    if params.get("solve", True):
        solved = a2p_solversystem.solveConstraints(document, useTransaction=False, showFailMessage=False)
    if previous_active is not None:
        App.setActiveDocument(previous_active)
        _maybe_bind_gui_document(Gui, previous_active)
    return _serialize_a2plus_constraint(constraint) | {"document": document.Name, "solved": solved}



def _cmd_create_body(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    body = document.addObject("PartDesign::Body", params.get("name", "Body"))
    if params.get("activate", True):
        _set_active_body(App, Gui, document, body)
    document.recompute()
    return _object_summary(body)



def _cmd_create_draft_array(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    base_object = document.getObject(params["base_object_name"])
    if base_object is None:
        raise RuntimeError(f"Base object '{params['base_object_name']}' not found")
    try:
        import Draft
    except Exception as exc:
        raise RuntimeError(f"Draft workbench is not available: {exc}") from exc

    array_mode = str(params.get("array_mode", "ortho")).lower()
    name = params.get("name") or "Array"
    use_link = bool(params.get("use_link", False))
    expressions = params.get("expressions") or {}

    if array_mode == "ortho":
        vector_x = _vector(App, params["vector_x"])
        vector_y = _vector(App, params["vector_y"])
        count_x = int(params["count_x"])
        count_y = int(params["count_y"])
        vector_z = params.get("vector_z")
        count_z = int(params.get("count_z", 1))
        if count_x < 1 or count_y < 1 or count_z < 1:
            raise RuntimeError("Draft ortho array counts must all be >= 1")
        if vector_z is not None or count_z != 1:
            array_obj = Draft.makeArray(
                base_object,
                vector_x,
                vector_y,
                _vector(App, vector_z or [0.0, 0.0, 1.0]),
                count_x,
                count_y,
                count_z,
                name=name,
                use_link=use_link,
            )
        else:
            array_obj = Draft.makeArray(
                base_object,
                vector_x,
                vector_y,
                count_x,
                count_y,
                name=name,
                use_link=use_link,
            )
        expression_property_map = {
            "count_x": "NumberX",
            "count_y": "NumberY",
            "count_z": "NumberZ",
        }
    elif array_mode == "polar":
        number = int(params.get("number", 5))
        if number < 1:
            raise RuntimeError("Draft polar array number must be >= 1")
        array_obj = Draft.make_polar_array(
            base_object,
            number=number,
            angle=float(params.get("angle", 360.0)),
            center=_vector(App, params.get("center") or [0.0, 0.0, 0.0]),
            use_link=use_link,
        )
        expression_property_map = {
            "number": "NumberPolar",
        }
    elif array_mode == "circular":
        number = int(params.get("number", 3))
        if number < 1:
            raise RuntimeError("Draft circular array number must be >= 1")
        array_obj = Draft.make_circular_array(
            base_object,
            r_distance=float(params.get("radial_distance", 100.0)),
            tan_distance=float(params.get("tangential_distance", 50.0)),
            number=number,
            symmetry=int(params.get("symmetry", 1)),
            axis=_vector(App, params.get("axis") or [0.0, 0.0, 1.0]),
            center=_vector(App, params.get("center") or [0.0, 0.0, 0.0]),
            use_link=use_link,
        )
        expression_property_map = {
            "number": "NumberPolar",
        }
    else:
        raise RuntimeError("Unsupported Draft array_mode. Supported: ortho, polar, circular")

    if getattr(array_obj, "Label", None) != name:
        array_obj.Label = name
    for key, property_name in expression_property_map.items():
        expression = expressions.get(key)
        if expression:
            array_obj.setExpression(property_name, str(expression))
    document.recompute()
    result = _object_summary(array_obj)
    result["array_type"] = getattr(array_obj, "ArrayType", None)
    result["count_x"] = getattr(array_obj, "NumberX", None)
    result["count_y"] = getattr(array_obj, "NumberY", None)
    result["count_z"] = getattr(array_obj, "NumberZ", None)
    result["number_polar"] = getattr(array_obj, "NumberPolar", None)
    result["number_circles"] = getattr(array_obj, "NumberCircles", None)
    if expressions:
        result["expressions"] = {k: v for k, v in expressions.items() if v}
    return result



def _cmd_create_pad(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    body = _resolve_body(document, params.get("body_name"))
    profile = document.getObject(params["sketch_name"])
    if profile is None:
        raise RuntimeError(f"Sketch '{params['sketch_name']}' not found")
    _ensure_sketch_profile_ready(profile, "create_pad")
    pad = document.addObject("PartDesign::Pad", params.get("name") or f"{profile.Name}Pad")
    body.addObject(pad)
    pad.Profile = profile
    pad.Length = float(params["length"])
    pad.Reversed = bool(params.get("reversed", False))
    pad.Midplane = bool(params.get("midplane", False))
    document.recompute()
    return _object_summary(pad)



def _cmd_create_parametric_primitive(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    kind = params["kind"].lower()
    type_id, property_map = _parametric_primitive_spec(kind)
    obj = document.getObject(params.get("name") or kind.title())
    if obj is None:
        obj = document.addObject(type_id, params.get("name") or kind.title())
    elif getattr(obj, "TypeId", None) != type_id:
        raise RuntimeError(f"Object '{obj.Name}' already exists but is not a {type_id}")

    property_values = params.get("params") or {}
    expressions = params.get("expressions") or {}

    for key, value in property_values.items():
        property_name = property_map.get(key.lower())
        if property_name is None:
            raise RuntimeError(f"Unsupported parameter '{key}' for parametric {kind}")
        setattr(obj, property_name, value)

    for key, expression in expressions.items():
        property_name = property_map.get(key.lower())
        if property_name is None:
            raise RuntimeError(f"Unsupported expression target '{key}' for parametric {kind}")
        obj.setExpression(property_name, str(expression))

    placement = _placement(App, params.get("placement"))
    if placement is not None and hasattr(obj, "Placement"):
        obj.Placement = placement
    document.recompute()
    result = _object_summary(obj)
    result["kind"] = kind
    result["type_id"] = type_id
    result["bound_properties"] = {
        property_name: str(getattr(obj, property_name))
        for property_name in property_map.values()
        if hasattr(obj, property_name)
    }
    return result



def _cmd_create_pocket(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    body = _resolve_body(document, params.get("body_name"))
    profile = document.getObject(params["sketch_name"])
    if profile is None:
        raise RuntimeError(f"Sketch '{params['sketch_name']}' not found")
    _ensure_sketch_profile_ready(profile, "create_pocket")
    pocket = document.addObject("PartDesign::Pocket", params.get("name") or f"{profile.Name}Pocket")
    body.addObject(pocket)
    pocket.Profile = profile
    if params.get("through_all", False):
        pocket.Type = 1
    else:
        pocket.Length = float(params["length"])
    pocket.Reversed = bool(params.get("reversed", False))
    pocket.Midplane = bool(params.get("midplane", False))
    document.recompute()
    return _object_summary(pocket)



def _cmd_create_primitive(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    kind = str(params.get("kind") or params.get("primitive_type") or "").lower()
    if not kind:
        raise RuntimeError("create_primitive requires 'kind' or 'primitive_type'")
    shape_params = dict(params.get("params") or {})
    # Backward compatibility for older callers that passed primitive dimensions at top level.
    for key in (
        "length",
        "width",
        "height",
        "radius",
        "radius1",
        "radius2",
        "pitch",
        "placement",
    ):
        if key in params and key not in shape_params:
            shape_params[key] = params[key]
    name = params.get("name") or kind.title()
    if kind == "box":
        shape = Part.makeBox(float(shape_params.get("length", 10.0)), float(shape_params.get("width", 10.0)), float(shape_params.get("height", 10.0)))
    elif kind == "cylinder":
        shape = Part.makeCylinder(float(shape_params.get("radius", 5.0)), float(shape_params.get("height", 10.0)))
    elif kind == "sphere":
        shape = Part.makeSphere(float(shape_params.get("radius", 5.0)))
    elif kind == "cone":
        shape = Part.makeCone(float(shape_params.get("radius1", 5.0)), float(shape_params.get("radius2", 0.0)), float(shape_params.get("height", 10.0)))
    elif kind == "torus":
        shape = Part.makeTorus(float(shape_params.get("radius1", 10.0)), float(shape_params.get("radius2", 2.0)))
    elif kind == "helix":
        shape = Part.makeHelix(float(shape_params.get("pitch", 1.0)), float(shape_params.get("height", 10.0)), float(shape_params.get("radius", 5.0)))
    else:
        raise RuntimeError(f"Unsupported primitive kind '{kind}'")
    feature = document.addObject("Part::Feature", name)
    if hasattr(feature, "Placement"):
        placement = _placement(App, shape_params.get("placement"))
        if placement is not None:
            feature.Placement = placement
    feature.Shape = shape
    document.recompute()
    return _object_summary(feature)



def _cmd_create_sketch(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    name = params["name"]
    sketch = document.getObject(name)
    body = None
    body_name = params.get("body_name")
    if body_name:
        body = _resolve_body(document, body_name)
    if sketch is None:
        sketch = document.addObject("Sketcher::SketchObject", name)
        if body is not None:
            body.addObject(sketch)
    else:
        while sketch.GeometryCount:
            sketch.delGeometry(sketch.GeometryCount - 1)
        while sketch.ConstraintCount:
            sketch.delConstraint(sketch.ConstraintCount - 1)

    for item in params.get("geometry", []):
        item_type = item["type"].lower()
        if item_type == "line":
            start = item["start"]
            end = item["end"]
            _add_line_segment(App, Part, sketch, start, end)
        elif item_type == "circle":
            center = item["center"]
            sketch.addGeometry(
                Part.Circle(
                    App.Vector(float(center[0]), float(center[1]), float(center[2] if len(center) > 2 else 0.0)),
                    App.Vector(0, 0, 1),
                    float(item["radius"]),
                ),
                False,
            )
        elif item_type == "arc":
            center = item["center"]
            sketch.addGeometry(
                Part.ArcOfCircle(
                    Part.Circle(
                        App.Vector(float(center[0]), float(center[1]), float(center[2] if len(center) > 2 else 0.0)),
                        App.Vector(0, 0, 1),
                        float(item["radius"]),
                    ),
                    float(item["start_angle"]),
                    float(item["end_angle"]),
                ),
                False,
            )
        elif item_type == "bspline":
            points = item["points"]
            if len(points) < 2:
                raise RuntimeError("BSpline geometry requires at least two points")
            curve = Part.BSplineCurve()
            curve.interpolate(
                [
                    App.Vector(
                        float(point[0]),
                        float(point[1]),
                        float(point[2] if len(point) > 2 else 0.0),
                    )
                    for point in points
                ],
                PeriodicFlag=bool(item.get("closed", False)),
            )
            sketch.addGeometry(curve, False)
        elif item_type == "polyline":
            points = item["points"]
            if len(points) < 2:
                raise RuntimeError("Polyline geometry requires at least two points")
            segment_indices = []
            for start, end in zip(points, points[1:]):
                segment_indices.append(_add_line_segment(App, Part, sketch, start, end))
            if item.get("constrain_coincident", True):
                for first_idx, second_idx in zip(segment_indices, segment_indices[1:]):
                    sketch.addConstraint(Sketcher.Constraint("Coincident", first_idx, 2, second_idx, 1))
            if item.get("closed", False):
                start = points[-1]
                end = points[0]
                closing_idx = _add_line_segment(App, Part, sketch, start, end)
                if item.get("constrain_coincident", True):
                    sketch.addConstraint(Sketcher.Constraint("Coincident", segment_indices[-1], 2, closing_idx, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", closing_idx, 2, segment_indices[0], 1))
        elif item_type == "rectangle":
            origin = item.get("origin", [0.0, 0.0, 0.0])
            width = float(item["width"])
            height = float(item["height"])
            z = float(origin[2] if len(origin) > 2 else 0.0)
            points = [
                [float(origin[0]), float(origin[1]), z],
                [float(origin[0]) + width, float(origin[1]), z],
                [float(origin[0]) + width, float(origin[1]) + height, z],
                [float(origin[0]), float(origin[1]) + height, z],
            ]
            segment_indices = []
            ordered_pairs = list(zip(points, points[1:] + points[:1]))
            for start, end in ordered_pairs:
                segment_indices.append(_add_line_segment(App, Part, sketch, start, end))
            if item.get("constrain_closed", True):
                for first_idx, second_idx in zip(segment_indices, segment_indices[1:] + segment_indices[:1]):
                    sketch.addConstraint(Sketcher.Constraint("Coincident", first_idx, 2, second_idx, 1))
            if item.get("constrain_orthogonal", True):
                for idx in (0, 2):
                    sketch.addConstraint(Sketcher.Constraint("Horizontal", segment_indices[idx]))
                for idx in (1, 3):
                    sketch.addConstraint(Sketcher.Constraint("Vertical", segment_indices[idx]))
            if item.get("lock_origin", False):
                sketch.addConstraint(Sketcher.Constraint("PointOnObject", segment_indices[0], 1, -1))
        else:
            raise RuntimeError(f"Unsupported sketch geometry '{item_type}'")

    for constraint in params.get("constraints", []):
        sketch.addConstraint(Sketcher.Constraint(constraint["type"], *constraint.get("args", [])))

    attachment_plane = params.get("attachment_plane")
    if body is not None and attachment_plane:
        plane = _body_origin_feature(body, attachment_plane)
        if "Support" in sketch.PropertiesList:
            sketch.Support = [(plane, "")]
        elif "AttachmentSupport" in sketch.PropertiesList:
            sketch.AttachmentSupport = [(plane, ("",))]
        else:
            raise RuntimeError(f"Sketch '{sketch.Name}' does not expose a support attachment property")
        sketch.MapMode = "FlatFace"

    placement = _placement(App, params.get("placement"))
    attachment_offset = _placement(App, params.get("attachment_offset"))
    if attachment_offset is not None and hasattr(sketch, "AttachmentOffset"):
        sketch.AttachmentOffset = attachment_offset
    elif placement is not None:
        sketch.Placement = placement
    sketch.validateConstraints()
    document.recompute()
    result = _object_summary(sketch)
    result["status"] = _sketch_status(sketch)
    return result



def _cmd_create_sketch_on_surface(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot create Curves sketch-on-surface features")
    document, sketch = _sketch_by_name(App, params)
    try:
        from freecad.Curves.Sketch_On_Surface import sketchOnSurface
    except Exception as exc:
        raise RuntimeError(f"Curves sketch-on-surface workflow is not available: {exc}") from exc
    feature = document.addObject("Part::FeaturePython", params.get("name") or "Sketch On Surface")
    sketchOnSurface(feature)
    feature.Sketch = sketch
    feature.FillFaces = bool(params.get("fill_faces", False))
    feature.FillExtrusion = bool(params.get("fill_extrusion", True))
    feature.Offset = float(params.get("offset", 0.0))
    feature.Thickness = float(params.get("thickness", 0.0))
    feature.ReverseU = bool(params.get("reverse_u", False))
    feature.ReverseV = bool(params.get("reverse_v", False))
    feature.SwapUV = bool(params.get("swap_uv", False))
    feature.ConstructionBounds = bool(params.get("construction_bounds", True))
    extra_objects = []
    for object_name in params.get("extra_object_names") or []:
        obj = document.getObject(object_name)
        if obj is None:
            raise RuntimeError(f"Extra object '{object_name}' not found in document '{document.Name}'")
        extra_objects.append(obj)
    feature.ExtraObjects = extra_objects
    document.recompute()
    return _object_summary(feature) | {
        "shape_is_null": bool(getattr(feature, "Shape", Part.Shape()).isNull()) if hasattr(feature, "Shape") else True,
        "fill_faces": bool(feature.FillFaces),
        "fill_extrusion": bool(feature.FillExtrusion),
        "offset": float(feature.Offset),
        "thickness": float(feature.Thickness),
    }



def _cmd_delete_a2plus_constraint(command, params, App, Gui, Part, Sketcher):
    _ensure_a2plus_support()
    import a2plib
    import a2p_solversystem

    document = _active_document(App, params.get("document_name"))
    constraint = _a2plus_constraint_by_name(document, params["constraint_name"])
    result = _serialize_a2plus_constraint(constraint)
    a2plib.removeConstraint(constraint)
    document.recompute()
    solved = None
    if params.get("solve", True):
        solved = a2p_solversystem.solveConstraints(document, useTransaction=False, showFailMessage=False)
    return {"document": document.Name, "deleted": result, "solved": solved}



def _cmd_delete_sketch_constraint(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    constraint_index = _resolve_constraint_index(
        sketch,
        constraint_index=params.get("constraint_index"),
        constraint_name=params.get("constraint_name"),
    )
    sketch.delConstraint(constraint_index)
    document.recompute()
    return {
        "sketch": sketch.Name,
        "deleted_constraint_index": constraint_index,
        "status": _sketch_status(sketch),
    }



def _cmd_dimension_circle_sketch(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    geometry_index = int(params.get("geometry_index", 0))
    circle = sketch.Geometry[geometry_index]
    radius_value = float(params.get("radius", float(circle.Radius)))
    center_x_value = float(params.get("center_x", circle.Center.x))
    center_y_value = float(params.get("center_y", circle.Center.y))

    radius_index = _add_named_constraint(
        sketch,
        Sketcher,
        "Radius",
        [geometry_index, radius_value],
        params.get("radius_constraint_name"),
    )

    center_x_index = None
    center_y_index = None
    if params.get("anchor_center", True):
        center_x_index = _add_named_constraint(
            sketch,
            Sketcher,
            "DistanceX",
            [geometry_index, 3, -1, 1, center_x_value],
            params.get("center_x_constraint_name"),
        )
        center_y_index = _add_named_constraint(
            sketch,
            Sketcher,
            "DistanceY",
            [geometry_index, 3, -1, 1, center_y_value],
            params.get("center_y_constraint_name"),
        )

    expression_map = params.get("expressions") or {}
    if expression_map.get("radius"):
        sketch.setExpression(f"Constraints[{radius_index}]", _normalize_constraint_expression(document, str(expression_map["radius"])))
    if center_x_index is not None and expression_map.get("center_x"):
        sketch.setExpression(f"Constraints[{center_x_index}]", _normalize_constraint_expression(document, str(expression_map["center_x"])))
    if center_y_index is not None and expression_map.get("center_y"):
        sketch.setExpression(f"Constraints[{center_y_index}]", _normalize_constraint_expression(document, str(expression_map["center_y"])))

    document.recompute()
    result = {
        "sketch": sketch.Name,
        "status": _sketch_status(sketch),
        "constraints": {
            "radius": {"index": radius_index, **_serialize_sketch_constraint(sketch.Constraints[radius_index])},
        },
    }
    if center_x_index is not None:
        result["constraints"]["center_x"] = {"index": center_x_index, **_serialize_sketch_constraint(sketch.Constraints[center_x_index])}
    if center_y_index is not None:
        result["constraints"]["center_y"] = {"index": center_y_index, **_serialize_sketch_constraint(sketch.Constraints[center_y_index])}
    return result



def _cmd_dimension_rectangle_sketch(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    bottom_line_index = int(params.get("bottom_line_index", 0))
    right_line_index = int(params.get("right_line_index", 1))
    anchor_line_index = int(params.get("anchor_line_index", 0))
    anchor_point_pos = int(params.get("anchor_point_pos", 1))

    bottom_line = sketch.Geometry[bottom_line_index]
    right_line = sketch.Geometry[right_line_index]
    anchor_point = bottom_line.StartPoint if anchor_point_pos == 1 else bottom_line.EndPoint

    width_value = float(params.get("width", abs(bottom_line.EndPoint.x - bottom_line.StartPoint.x)))
    height_value = float(params.get("height", abs(right_line.EndPoint.y - right_line.StartPoint.y)))
    origin_x_value = float(params.get("origin_x", anchor_point.x))
    origin_y_value = float(params.get("origin_y", anchor_point.y))

    width_index = _add_named_constraint(
        sketch,
        Sketcher,
        "DistanceX",
        [bottom_line_index, 1, bottom_line_index, 2, width_value],
        params.get("width_constraint_name"),
    )
    height_index = _add_named_constraint(
        sketch,
        Sketcher,
        "DistanceY",
        [right_line_index, 1, right_line_index, 2, height_value],
        params.get("height_constraint_name"),
    )

    origin_x_index = None
    origin_y_index = None
    if params.get("anchor_lower_left", True):
        origin_x_index = _add_named_constraint(
            sketch,
            Sketcher,
            "DistanceX",
            [anchor_line_index, anchor_point_pos, -1, 1, origin_x_value],
            params.get("origin_x_constraint_name"),
        )
        origin_y_index = _add_named_constraint(
            sketch,
            Sketcher,
            "DistanceY",
            [anchor_line_index, anchor_point_pos, -1, 1, origin_y_value],
            params.get("origin_y_constraint_name"),
        )

    expression_map = params.get("expressions") or {}
    if expression_map.get("width"):
        sketch.setExpression(f"Constraints[{width_index}]", _normalize_constraint_expression(document, str(expression_map["width"])))
    if expression_map.get("height"):
        sketch.setExpression(f"Constraints[{height_index}]", _normalize_constraint_expression(document, str(expression_map["height"])))
    if origin_x_index is not None and expression_map.get("origin_x"):
        sketch.setExpression(f"Constraints[{origin_x_index}]", _normalize_constraint_expression(document, str(expression_map["origin_x"])))
    if origin_y_index is not None and expression_map.get("origin_y"):
        sketch.setExpression(f"Constraints[{origin_y_index}]", _normalize_constraint_expression(document, str(expression_map["origin_y"])))

    document.recompute()
    result = {
        "sketch": sketch.Name,
        "status": _sketch_status(sketch),
        "constraints": {
            "width": {"index": width_index, **_serialize_sketch_constraint(sketch.Constraints[width_index])},
            "height": {"index": height_index, **_serialize_sketch_constraint(sketch.Constraints[height_index])},
        },
    }
    if origin_x_index is not None:
        result["constraints"]["origin_x"] = {"index": origin_x_index, **_serialize_sketch_constraint(sketch.Constraints[origin_x_index])}
    if origin_y_index is not None:
        result["constraints"]["origin_y"] = {"index": origin_y_index, **_serialize_sketch_constraint(sketch.Constraints[origin_y_index])}
    return result



def _cmd_execute_code(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    namespace = {
        "App": App,
        "Gui": Gui,
        "Part": Part,
        "Sketcher": Sketcher,
        "doc": document,
        "document": document,
        "result": None,
    }
    exec(params["code"], namespace, namespace)
    if params.get("recompute", True):
        document.recompute()
    return {
        "document": document.Name,
        "result_repr": repr(namespace.get("result")),
        "locals": sorted(k for k in namespace.keys() if not k.startswith("__")),
    }



def _cmd_extrude_sketch(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    source = document.getObject(params["sketch_name"])
    if source is None:
        raise RuntimeError(f"Sketch '{params['sketch_name']}' not found")
    extrusion = document.addObject("Part::Extrusion", params.get("name") or f"{source.Name}Extrusion")
    extrusion.Base = source
    direction = params.get("direction") or [0.0, 0.0, 1.0]
    extrusion.Dir = App.Vector(float(direction[0]), float(direction[1]), float(direction[2]))
    extrusion.LengthFwd = float(params["length"])
    extrusion.LengthRev = float(params["length"]) if params.get("symmetric", False) else 0.0
    extrusion.Solid = bool(params.get("solid", True))
    extrusion.Reversed = bool(params.get("reversed", False))
    document.recompute()
    return _object_summary(extrusion)



def _cmd_fit_view(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot fit the active view")
    document = _active_document(App, params.get("document_name"))
    active_view = _active_view_for_document(Gui, document.Name)
    active_view.fitAll()
    return {"document": document.Name, "fit_view": True}



def _cmd_get_document_tree(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    return _document_tree(
        document,
        include_properties=bool(params.get("include_properties", False)),
        property_names=params.get("property_names"),
        max_depth=int(params.get("max_depth", 4)),
        root_name=params.get("root_name"),
    )



def _cmd_get_object_topology(command, params, App, Gui, Part, Sketcher):
    document, obj = _object_by_name(App, params)
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise RuntimeError(f"Object '{obj.Name}' has no usable shape for topology inspection")
    return {
        "document": document.Name,
        "object": _object_summary(obj),
        **_serialize_shape_topology(shape, include_vertices=bool(params.get("include_vertices", True)),
                                    summary_only=bool(params.get("summary_only", False))),
    }



def _cmd_get_sketch_details(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    return _sketch_details(sketch)



def _cmd_get_sketch_status(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    return _sketch_status(sketch)



def _cmd_get_spreadsheet_cells(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    sheet = document.getObject(params["name"])
    if sheet is None:
        raise RuntimeError(f"Spreadsheet '{params['name']}' not found")
    result = {}
    for cell in params.get("cells", []):
        result[cell] = sheet.get(cell)
    return {"sheet": sheet.Name, "cells": result}



def _cmd_import_a2plus_part(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot import A2plus assembly parts")
    document = _active_document(App, params.get("document_name"))
    if not getattr(document, "FileName", ""):
        raise RuntimeError("A2plus import requires the target assembly document to be saved first")
    _ensure_a2plus_support()
    import a2p_importpart

    source_path = str(Path(params["path"]).expanduser())
    with _temporary_a2plus_import_invisible_shapes_disabled():
        imported = a2p_importpart.importPartFromFile(
            document,
            source_path,
            extractSingleShape=bool(params.get("extract_single_shape", False)),
            desiredShapeLabel=params.get("desired_shape_label"),
        )
    if imported is None:
        raise RuntimeError(f"A2plus did not import a component from '{source_path}'")
    requested_name = params.get("name")
    if requested_name:
        imported.Label = str(requested_name)
    fixed_position = params.get("fixed_position")
    if fixed_position is not None and hasattr(imported, "fixedPosition"):
        imported.fixedPosition = bool(fixed_position)
    document.recompute()
    return _object_summary(imported) | {
        "source_file": getattr(imported, "sourceFile", None),
        "source_part": getattr(imported, "sourcePart", None),
        "fixed_position": getattr(imported, "fixedPosition", None),
        "subassembly_import": getattr(imported, "subassemblyImport", None),
    }



def _cmd_import_library_object(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    entry_dir = Path(params["library_root"]).expanduser() / params["entry_id"]
    metadata = _read_library_metadata(entry_dir, params["entry_id"])
    brep_path = entry_dir / metadata["storage"]["shape_file"]
    if not brep_path.exists():
        raise RuntimeError(f"Library entry '{params['entry_id']}' is missing {metadata['storage']['shape_file']}")
    shape = Part.Shape()
    shape.importBrep(str(brep_path))
    feature = document.addObject("Part::Feature", params.get("object_name") or params["entry_id"])
    feature.Shape = shape
    document.recompute()
    result = _object_summary(feature)
    result["library_metadata"] = metadata
    return result



def _cmd_isolate_objects(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot isolate objects")
    document = _active_document(App, params.get("document_name"))
    _active_view_for_document(Gui, document.Name)
    object_names = set(params.get("object_names") or [])
    if not object_names:
        raise RuntimeError("isolate_objects requires at least one object name")
    updated = []
    missing = []
    for obj in document.Objects:
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None or not hasattr(view_object, "Visibility"):
            continue
        view_object.Visibility = obj.Name in object_names
        updated.append({"name": obj.Name, "visible": bool(view_object.Visibility)})
    for object_name in object_names:
        if document.getObject(object_name) is None:
            missing.append(object_name)
    document.recompute()
    return {"document": document.Name, "updated": updated, "missing": missing}



def _cmd_list_a2plus_constraints(command, params, App, Gui, Part, Sketcher):
    _ensure_a2plus_support()
    document = _active_document(App, params.get("document_name"))
    constraints = []
    for obj in document.Objects:
        if "ConstraintInfo" not in getattr(obj, "Content", ""):
            continue
        constraints.append(_serialize_a2plus_constraint(obj))
    constraints.sort(key=lambda entry: entry["name"])
    return {"document": document.Name, "constraints": constraints}



def _cmd_list_a2plus_importable_shapes(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot inspect A2plus import candidates")
    payload = _list_a2plus_importable_shapes(App, str(params["path"]))
    payload["document"] = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    return payload



def _cmd_list_a2plus_parts(command, params, App, Gui, Part, Sketcher):
    _ensure_a2plus_support()
    import a2plib

    document = _active_document(App, params.get("document_name"))
    parts = []
    for obj in document.Objects:
        if not a2plib.isA2pPart(obj):
            continue
        parts.append(
            _object_summary(obj)
            | {
                "source_file": getattr(obj, "sourceFile", None),
                "source_part": getattr(obj, "sourcePart", None),
                "fixed_position": getattr(obj, "fixedPosition", None),
                "subassembly_import": getattr(obj, "subassemblyImport", None),
            }
        )
    parts.sort(key=lambda entry: entry["name"])
    return {"document": document.Name, "parts": parts}



def _cmd_list_documents(command, params, App, Gui, Part, Sketcher):
    docs = list(App.listDocuments().keys())
    active = _current_active_document_name(App)
    return {"documents": docs, "active_document": active}



def _cmd_list_helpers(command, params, App, Gui, Part, Sketcher):
    helpers = [spec.to_dict() for spec in _helper_registry().values()]
    helpers.sort(key=lambda entry: entry["name"])
    return {"helpers": helpers}



def _cmd_list_library_objects(command, params, App, Gui, Part, Sketcher):
    root = Path(params["library_root"]).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            metadata = _read_library_metadata(child, child.name)
            entries.append({"entry_id": child.name, "metadata": metadata, "valid": True})
        except Exception as exc:
            entries.append({"entry_id": child.name, "metadata": None, "valid": False, "error": str(exc)})
    return {"entries": entries}



def _cmd_new_document(command, params, App, Gui, Part, Sketcher):
    document = App.newDocument(params.get("name"))
    if params.get("activate", True):
        App.setActiveDocument(document.Name)
        _maybe_bind_gui_document(Gui, document.Name)
    return {"document": document.Name}



def _cmd_open_document(command, params, App, Gui, Part, Sketcher):
    path = str(Path(params["path"]).expanduser())
    document = App.openDocument(path)
    if document is None:
        raise RuntimeError(f"Failed to open FreeCAD document at '{path}'")
    if params.get("activate", True):
        App.setActiveDocument(document.Name)
        _maybe_bind_gui_document(Gui, document.Name)
    return {
        "document": document.Name,
        "path": path,
        "label": getattr(document, "Label", document.Name),
    }



def _cmd_rename_sketch_constraint(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    constraint_index = _resolve_constraint_index(
        sketch,
        constraint_index=params.get("constraint_index"),
        constraint_name=params.get("constraint_name"),
    )
    new_name = str(params["new_name"])
    sketch.renameConstraint(constraint_index, new_name)
    document.recompute()
    return {
        "sketch": sketch.Name,
        "constraint_index": constraint_index,
        "constraint_name": new_name,
        "status": _sketch_status(sketch),
        "constraint": {"index": int(constraint_index), **_serialize_sketch_constraint(sketch.Constraints[constraint_index])},
    }



def _cmd_run_helper(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    helper_name = params["name"]
    helper = _helper_registry().get(helper_name)
    if helper is None:
        raise RuntimeError(f"Unknown helper '{helper_name}'")
    if not helper.available:
        raise RuntimeError(f"Helper '{helper_name}' is not available in the current FreeCAD environment")
    if helper.gui_required and Gui is None:
        raise RuntimeError(f"Helper '{helper_name}' requires a running FreeCAD GUI session")
    feature = helper.runner(App, Gui, Part, document, params.get("params") or {})
    document.recompute()
    return _object_summary(feature)



def _cmd_save_document(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    path = params.get("path")
    if path:
        target_path = str(Path(path).expanduser())
        document.saveAs(target_path)
    else:
        target_path = getattr(document, "FileName", "") or None
        if not target_path:
            raise RuntimeError(f"Document '{document.Name}' has no file path yet; provide 'path'")
        document.save()
    return {
        "document": document.Name,
        "path": target_path,
    }



def _cmd_set_active_body(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    body = _resolve_body(document, params["body_name"])
    _set_active_body(App, Gui, document, body)
    return _object_summary(body)



def _cmd_set_active_document(command, params, App, Gui, Part, Sketcher):
    name = params["name"]
    document = App.getDocument(name)
    if document is None:
        raise RuntimeError(f"Document '{name}' not found")
    App.setActiveDocument(name)
    _maybe_bind_gui_document(Gui, name)
    return {"document": name}



def _cmd_set_constraint_expression(command, params, App, Gui, Part, Sketcher):
    document, sketch = _sketch_by_name(App, params)
    constraint_index = _resolve_constraint_index(
        sketch,
        constraint_index=params.get("constraint_index"),
        constraint_name=params.get("constraint_name"),
    )
    property_path = f"Constraints[{constraint_index}]"
    sketch.setExpression(property_path, _normalize_constraint_expression(document, params["expression"]))
    document.recompute()
    return {
        "sketch": sketch.Name,
        "constraint_index": constraint_index,
        "constraint_name": getattr(sketch.Constraints[constraint_index], "Name", ""),
        "property_path": property_path,
        "expression": params["expression"],
        "status": _sketch_status(sketch),
    }



def _cmd_set_objects_visibility(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot change object visibility")
    document = _active_document(App, params.get("document_name"))
    _active_view_for_document(Gui, document.Name)
    object_names = params.get("object_names") or []
    if not object_names:
        raise RuntimeError("set_objects_visibility requires at least one object name")
    result = _set_objects_visibility(document, object_names, bool(params.get("visible", True)))
    document.recompute()
    return {"document": document.Name, **result}



def _cmd_set_sketch_expression(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    sketch = document.getObject(params["sketch_name"])
    if sketch is None:
        raise RuntimeError(f"Sketch '{params['sketch_name']}' not found")
    property_path = params["property_path"]
    expression = params["expression"]
    sketch.setExpression(property_path, expression)
    document.recompute()
    return {
        "sketch": sketch.Name,
        "property_path": property_path,
        "expression": expression,
    }



def _cmd_set_spreadsheet_cells(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    sheet = document.getObject(params["name"])
    if sheet is None:
        sheet = document.addObject("Spreadsheet::Sheet", params["name"])
    for cell, value in (params.get("cells") or {}).items():
        sheet.set(cell, str(value))
    for cell, alias in (params.get("alias_map") or {}).items():
        sheet.setAlias(cell, alias)
    document.recompute()
    return {"sheet": sheet.Name, "cells_written": sorted((params.get("cells") or {}).keys())}



def _cmd_set_view_orientation(command, params, App, Gui, Part, Sketcher):
    if Gui is None:
        raise RuntimeError("FreeCADGui is not available, cannot change the active view orientation")
    document = _active_document(App, params.get("document_name"))
    active_view = _active_view_for_document(Gui, document.Name)
    method_name = _set_view_orientation(active_view, params["orientation"])
    if params.get("fit_view", False):
        active_view.fitAll()
    return {
        "document": document.Name,
        "orientation": params["orientation"],
        "method": method_name,
        "fit_view": bool(params.get("fit_view", False)),
    }



def _cmd_store_file_object_to_library(command, params, App, Gui, Part, Sketcher):
    document = None
    should_close = False
    try:
        document, should_close, normalized_path = _open_document_for_inspection(App, Gui, params["path"])
        obj = document.getObject(params["object_name"])
        if obj is None:
            raise RuntimeError(f"Object '{params['object_name']}' not found in '{normalized_path}'")
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            raise RuntimeError(f"Object '{obj.Name}' in '{normalized_path}' has no usable shape to store")
        entry_dir = _ensure_library_entry(params["library_root"], params["entry_id"], bool(params.get("overwrite", False)))
        brep_path = _library_shape_path(entry_dir)
        metadata_path = _library_metadata_path(entry_dir)
        shape.exportBrep(str(brep_path))
        user_metadata = dict(params.get("metadata") or {})
        user_metadata.setdefault("source_file", normalized_path)
        metadata = _build_library_metadata(params["entry_id"], document.Name, obj, user_metadata)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return {"entry_id": params["entry_id"], "path": str(entry_dir), "metadata": metadata}
    finally:
        _close_temp_document(App, document, should_close)



def _cmd_store_library_object(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    obj = document.getObject(params["object_name"])
    if obj is None:
        raise RuntimeError(f"Object '{params['object_name']}' not found")
    entry_dir = _ensure_library_entry(params["library_root"], params["entry_id"], bool(params.get("overwrite", False)))
    brep_path = _library_shape_path(entry_dir)
    metadata_path = _library_metadata_path(entry_dir)
    obj.Shape.exportBrep(str(brep_path))
    metadata = _build_library_metadata(params["entry_id"], document.Name, obj, params.get("metadata") or {})
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return {"entry_id": params["entry_id"], "path": str(entry_dir), "metadata": metadata}



def _cmd_sweep_profile(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    profile = document.getObject(params["profile_name"])
    path = document.getObject(params["path_name"])
    if profile is None:
        raise RuntimeError(f"Profile '{params['profile_name']}' not found")
    if path is None:
        raise RuntimeError(f"Path '{params['path_name']}' not found")
    sweep = document.addObject("Part::Sweep", params.get("name") or f"{profile.Name}Sweep")
    sweep.Sections = [profile]
    sweep.Spine = (path, [])
    sweep.Solid = bool(params.get("solid", True))
    sweep.Frenet = bool(params.get("frenet", False))
    if params.get("transition") is not None:
        sweep.Transition = params["transition"]
    document.recompute()
    if hasattr(sweep, "Shape") and sweep.Shape.isNull():
        raise RuntimeError(f"Sweep '{sweep.Name}' recomputed to a null shape; state={list(getattr(sweep, 'State', []))}")
    return _object_summary(sweep)



def _cmd_update_a2plus_constraint(command, params, App, Gui, Part, Sketcher):
    _ensure_a2plus_support()
    import a2p_solversystem

    document = _active_document(App, params.get("document_name"))
    constraint = _a2plus_constraint_by_name(document, params["constraint_name"])

    if params.get("name") is not None:
        constraint.Label = str(params["name"])
    if params.get("direction_constraint") is not None and hasattr(constraint, "directionConstraint"):
        constraint.directionConstraint = str(params["direction_constraint"])
    if params.get("offset") is not None and hasattr(constraint, "offset"):
        constraint.offset = float(params["offset"])
    if params.get("angle") is not None and hasattr(constraint, "angle"):
        constraint.angle = float(params["angle"])
    if params.get("lock_rotation") is not None and hasattr(constraint, "lockRotation"):
        constraint.lockRotation = bool(params["lock_rotation"])
    if params.get("suppressed") is not None and hasattr(constraint, "Suppressed"):
        constraint.Suppressed = bool(params["suppressed"])

    document.recompute()
    solved = None
    if params.get("solve", True):
        solved = a2p_solversystem.solveConstraints(document, useTransaction=False, showFailMessage=False)
    return _serialize_a2plus_constraint(constraint) | {"document": document.Name, "solved": solved}



def _cmd_validate_pose_sweep(command, params, App, Gui, Part, Sketcher):
    document = _active_document(App, params.get("document_name"))
    return _validate_pose_sweep_payload(App, document, params.get("manifest"))



def _current_active_document_name(App) -> Optional[str]:
    active_document = getattr(App, "ActiveDocument", None)
    active_name = str(getattr(active_document, "Name", "") or "").strip()
    if not active_name:
        return None
    try:
        documents = dict(getattr(App, "listDocuments")() or {})
    except Exception:
        documents = {}
    if documents and active_name not in documents:
        return None
    get_document = getattr(App, "getDocument", None)
    if callable(get_document):
        try:
            if get_document(active_name) is None:
                return None
        except Exception:
            pass
    return active_name



def _list_a2plus_importable_shapes(FreeCAD, source_path: str) -> Dict[str, Any]:
    _ensure_a2plus_support()
    import a2p_importpart

    previous_active = getattr(getattr(FreeCAD, "ActiveDocument", None), "Name", None)
    import_doc = None
    should_close = False
    normalized_path = str(Path(source_path).expanduser())
    try:
        import_doc, should_close, normalized_path = _open_a2plus_source_document(FreeCAD, normalized_path)
        with _temporary_a2plus_import_invisible_shapes_disabled():
            topo_mapper = a2p_importpart.TopoMapper(import_doc)
            importable_objects = list(topo_mapper.getTopLevelObjects(allowSketches=True) or [])
        shapes = [
            {
                "object_name": str(getattr(obj, "Name", "") or ""),
                "label": str(getattr(obj, "Label", "") or getattr(obj, "Name", "") or ""),
                "type_id": getattr(obj, "TypeId", None),
                "is_visible": bool(getattr(getattr(obj, "ViewObject", None), "Visibility", True)),
            }
            for obj in importable_objects
        ]
        shapes.sort(key=lambda entry: (entry["label"], entry["object_name"]))
        return {
            "path": normalized_path,
            "source_document": getattr(import_doc, "Name", None),
            "source_document_label": getattr(import_doc, "Label", getattr(import_doc, "Name", None)),
            "reused_open_document": not should_close,
            "shape_count": len(shapes),
            "shapes": shapes,
        }
    finally:
        if should_close and import_doc is not None:
            try:
                FreeCAD.closeDocument(import_doc.Name)
            except Exception:
                pass
        if previous_active:
            try:
                FreeCAD.setActiveDocument(previous_active)
            except Exception:
                pass



def _open_a2plus_source_document(FreeCAD, source_path: str):
    normalized_path = str(Path(source_path).expanduser())
    requested_file = os.path.split(normalized_path)[1]
    existing = None
    for document in FreeCAD.listDocuments().values():
        recent_file = os.path.split(getattr(document, "FileName", "") or "")[1]
        if requested_file == recent_file:
            existing = document
            break
    if existing is not None:
        return existing, False, normalized_path

    if normalized_path.lower().endswith(".fcstd"):
        import_doc = FreeCAD.openDocument(normalized_path)
    elif normalized_path.lower().endswith(".stp") or normalized_path.lower().endswith(".step"):
        import ImportGui

        name_hint = os.path.splitext(os.path.basename(normalized_path))[0] or "ImportedStep"
        FreeCAD.newDocument(name_hint)
        new_name = FreeCAD.ActiveDocument.Name
        FreeCAD.setActiveDocument(new_name)
        ImportGui.insert(normalized_path, new_name)
        import_doc = FreeCAD.ActiveDocument
    else:
        raise RuntimeError("A2plus can only inspect/import parts from '*.FCStd', '*.step', or '*.stp' files")

    if import_doc is None:
        raise RuntimeError(f"Failed to open A2plus source file '{normalized_path}'")
    return import_doc, True, normalized_path



def _patch_a2plus_solver_dialog_suppression() -> None:
    import a2p_solversystem

    if getattr(a2p_solversystem, "_mcpfreecad_dialog_suppression_installed", False):
        return

    original = a2p_solversystem.solveConstraints
    setattr(a2p_solversystem, "_mcpfreecad_original_solveConstraints", original)

    def _solve_constraints_without_popup(*args, **kwargs):
        if "showFailMessage" not in kwargs and len(args) < 5:
            kwargs["showFailMessage"] = False
        return original(*args, **kwargs)

    a2p_solversystem.solveConstraints = _solve_constraints_without_popup
    a2p_solversystem._mcpfreecad_dialog_suppression_installed = True



def _pose_sweep_contact_group_spec(raw: dict[str, Any]) -> dict[str, Any]:
    moving = [str(item or "").strip() for item in list(raw.get("moving_objects") or [])]
    candidates = [str(item or "").strip() for item in list(raw.get("candidate_objects") or [])]
    if not moving or not candidates or not all(moving) or not all(candidates):
        raise RuntimeError("Each pose-sweep contact group requires moving_objects and candidate_objects")
    maximum = raw.get("max_clearance_mm")
    if maximum is None:
        raise RuntimeError("Each pose-sweep contact group requires max_clearance_mm")
    minimum_engaged = int(raw.get("min_engaged_per_moving_object") or 1)
    if minimum_engaged < 1 or minimum_engaged > len(candidates):
        raise RuntimeError("min_engaged_per_moving_object must be between one and the candidate count")
    return {
        "id": str(raw.get("id") or "contact_group").strip(),
        "moving_objects": moving,
        "candidate_objects": candidates,
        "min_engaged_per_moving_object": minimum_engaged,
        "max_clearance_mm": max(0.0, float(maximum)),
        "max_interference_mm3": max(0.0, float(raw.get("max_interference_mm3") or 0.0)),
    }



def _pose_sweep_pair_spec(raw: dict[str, Any]) -> dict[str, Any]:
    objects = raw.get("objects") or [raw.get("object_a"), raw.get("object_b")]
    if not isinstance(objects, (list, tuple)) or len(objects) != 2 or not all(str(item or "").strip() for item in objects):
        raise RuntimeError("Each pose-sweep interface requires exactly two object names")
    kind = str(raw.get("kind") or "forbidden_clearance").strip().lower()
    if kind not in {"forbidden_clearance", "allowed_contact", "contained_in"}:
        raise RuntimeError("Pose-sweep interface kind must be forbidden_clearance, allowed_contact, or contained_in")
    return {
        "id": str(raw.get("id") or f"{objects[0]}__{objects[1]}").strip(),
        "objects": [str(objects[0]), str(objects[1])],
        "kind": kind,
        "min_clearance_mm": max(0.0, float(raw.get("min_clearance_mm") or 0.0)),
        "max_clearance_mm": None if raw.get("max_clearance_mm") is None else max(0.0, float(raw.get("max_clearance_mm"))),
        "max_interference_mm3": max(0.0, float(raw.get("max_interference_mm3") or 0.0)),
        "min_containment_ratio": min(1.0, max(0.0, float(raw.get("min_containment_ratio", 1.0)))),
    }



def _pose_sweep_transform_shape(App, shape, transform: Optional[dict[str, Any]]):
    """Return a transformed copy without modifying the document object."""
    copied = shape.copy()
    payload = dict(transform or {})
    rotation = dict(payload.get("rotation") or {})
    axis = rotation.get("axis") or payload.get("rotation_axis")
    angle = rotation.get("angle_deg", payload.get("angle_deg", 0.0))
    origin = rotation.get("origin") or payload.get("rotation_origin") or [0.0, 0.0, 0.0]
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        raise RuntimeError("Pose rotation origin must contain exactly three values")
    origin_vector = App.Vector(float(origin[0]), float(origin[1]), float(origin[2]))
    if axis is not None:
        if not isinstance(axis, (list, tuple)) or len(axis) != 3:
            raise RuntimeError("Pose rotation axis must contain exactly three values")
        axis_vector = App.Vector(float(axis[0]), float(axis[1]), float(axis[2]))
        if axis_vector.Length <= 1e-12:
            raise RuntimeError("Pose rotation axis must be non-zero")
        copied.rotate(origin_vector, axis_vector, float(angle or 0.0))
    elif abs(float(angle or 0.0)) > 1e-12:
        raise RuntimeError("Pose rotation angle requires an axis")
    translation = payload.get("translation") or [0.0, 0.0, 0.0]
    if not isinstance(translation, (list, tuple)) or len(translation) != 3:
        raise RuntimeError("Pose translation must contain exactly three values")
    copied.translate(App.Vector(float(translation[0]), float(translation[1]), float(translation[2])))
    return copied



def _snapshot_library_root(params: Dict[str, Any]) -> Path:
    """Use the MCP library default when a native bridge client omits it."""
    configured = params.get("library_root")
    if configured:
        return Path(str(configured)).expanduser()
    return Path.home() / "freecad-mcp-library"



def _snapshot_output_path(filename: Optional[str], library_root: str, default_name: str) -> Path:
    snapshot_dir = Path(library_root).expanduser() / "snapshots"
    if filename:
        candidate = Path(filename).expanduser()
        if not candidate.is_absolute():
            candidate = snapshot_dir / candidate
        return candidate
    return snapshot_dir / default_name



@contextmanager
def _temporary_a2plus_import_invisible_shapes_disabled():
    App, _, _, _ = _require_freecad_modules()
    import a2plib

    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/A2plus")
    original = preferences.GetBool("doNotImportInvisibleShapes", True)
    preferences.SetBool("doNotImportInvisibleShapes", False)
    try:
        yield
    finally:
        preferences.SetBool("doNotImportInvisibleShapes", original)



def _validate_pose_sweep_payload(App, document, manifest: dict[str, Any]) -> dict[str, Any]:
    """Evaluate declared mechanical interfaces across immutable pose copies."""
    if not isinstance(manifest, dict):
        raise RuntimeError("validate_pose_sweep requires a manifest object")
    poses = list(manifest.get("poses") or [])
    interfaces = [_pose_sweep_pair_spec(dict(item)) for item in list(manifest.get("interfaces") or []) if isinstance(item, dict)]
    contact_groups = [_pose_sweep_contact_group_spec(dict(item)) for item in list(manifest.get("contact_groups") or []) if isinstance(item, dict)]
    if not poses:
        raise RuntimeError("Pose-sweep manifest requires at least one pose")
    if not interfaces and not contact_groups:
        raise RuntimeError("Pose-sweep manifest requires at least one interface or contact group")
    evaluation_count = len(interfaces) + sum(len(item["moving_objects"]) * len(item["candidate_objects"]) for item in contact_groups)
    if len(poses) * evaluation_count > 4096:
        raise RuntimeError("Pose-sweep request exceeds 4096 interface evaluations; split or refine it in stages")
    object_names = {name for item in interfaces for name in item["objects"]}
    object_names.update(name for item in contact_groups for name in item["moving_objects"] + item["candidate_objects"])
    for pose in poses:
        if not isinstance(pose, dict) or not str(pose.get("id") or "").strip():
            raise RuntimeError("Each pose requires a stable non-empty id")
        transforms = pose.get("transforms") or {}
        if not isinstance(transforms, dict):
            raise RuntimeError(f"Pose '{pose.get('id')}' transforms must be an object")
        object_names.update(str(name) for name in transforms)
    source_shapes = {}
    for name in object_names:
        obj = document.getObject(name)
        shape = getattr(obj, "Shape", None) if obj is not None else None
        if shape is None or shape.isNull():
            raise RuntimeError(f"Pose-sweep object '{name}' has no usable shape")
        source_shapes[name] = shape

    summaries = {
        item["id"]: {
            "interface": item,
            "minimum_distance_mm": None,
            "maximum_distance_mm": 0.0,
            "maximum_interference_mm3": 0.0,
            "minimum_containment_ratio": None,
            "failing_pose_ids": [],
            "evaluated_pose_count": 0,
        }
        for item in interfaces
    }
    group_summaries = {
        item["id"]: {
            "contact_group": item,
            "minimum_engaged_count": None,
            "minimum_candidate_distance_mm": None,
            "maximum_interference_mm3": 0.0,
            "failing_pose_ids": [],
            "evaluated_pose_count": 0,
        }
        for item in contact_groups
    }
    failures = []
    for pose_index, pose in enumerate(poses):
        pose_id = str(pose["id"])
        transforms = dict(pose.get("transforms") or {})
        posed_shapes = {
            name: _pose_sweep_transform_shape(App, shape, transforms.get(name))
            for name, shape in source_shapes.items()
        }
        for item in interfaces:
            left, right = (posed_shapes[name] for name in item["objects"])
            distance = float(left.distToShape(right)[0])
            interference = 0.0
            # Containment needs the common volume even when the contained body
            # does not touch the cavity boundary.
            if distance <= 1e-9 or item["kind"] == "contained_in":
                interference = max(0.0, float(left.common(right).Volume))
            summary = summaries[item["id"]]
            summary["minimum_distance_mm"] = distance if summary["minimum_distance_mm"] is None else min(summary["minimum_distance_mm"], distance)
            summary["maximum_distance_mm"] = max(float(summary["maximum_distance_mm"]), distance)
            summary["maximum_interference_mm3"] = max(float(summary["maximum_interference_mm3"]), interference)
            summary["evaluated_pose_count"] += 1
            failures_here = []
            if item["kind"] == "forbidden_clearance":
                if distance + 1e-9 < item["min_clearance_mm"]:
                    failures_here.append("clearance_below_minimum")
                if interference > item["max_interference_mm3"] + 1e-9:
                    failures_here.append("forbidden_interference")
            elif item["kind"] == "allowed_contact":
                maximum = item["max_clearance_mm"]
                if maximum is None:
                    raise RuntimeError(f"Allowed-contact interface '{item['id']}' requires max_clearance_mm")
                if distance > maximum + 1e-9:
                    failures_here.append("engagement_gap_exceeds_limit")
                if interference > item["max_interference_mm3"] + 1e-9:
                    failures_here.append("allowed_contact_interference_exceeds_limit")
            else:
                contained_volume = max(0.0, float(getattr(left, "Volume", 0.0) or 0.0))
                containment_ratio = 0.0 if contained_volume <= 1e-12 else min(1.0, interference / contained_volume)
                prior_ratio = summary["minimum_containment_ratio"]
                summary["minimum_containment_ratio"] = containment_ratio if prior_ratio is None else min(float(prior_ratio), containment_ratio)
                if containment_ratio + 1e-9 < item["min_containment_ratio"]:
                    failures_here.append("contained_body_escapes_cavity")
            if failures_here:
                summary["failing_pose_ids"].append(pose_id)
                failure = {"pose_id": pose_id, "interface_id": item["id"], "reasons": failures_here, "distance_mm": distance, "interference_mm3": interference}
                if item["kind"] == "contained_in":
                    failure["containment_ratio"] = containment_ratio
                failures.append(failure)
        for item in contact_groups:
            summary = group_summaries[item["id"]]
            group_failures = []
            for moving_name in item["moving_objects"]:
                engaged = 0
                for candidate_name in item["candidate_objects"]:
                    distance = float(posed_shapes[moving_name].distToShape(posed_shapes[candidate_name])[0])
                    interference = 0.0
                    if distance <= 1e-9:
                        interference = max(0.0, float(posed_shapes[moving_name].common(posed_shapes[candidate_name]).Volume))
                    summary["minimum_candidate_distance_mm"] = distance if summary["minimum_candidate_distance_mm"] is None else min(summary["minimum_candidate_distance_mm"], distance)
                    summary["maximum_interference_mm3"] = max(float(summary["maximum_interference_mm3"]), interference)
                    if distance <= item["max_clearance_mm"] + 1e-9 and interference <= item["max_interference_mm3"] + 1e-9:
                        engaged += 1
                if engaged < item["min_engaged_per_moving_object"]:
                    group_failures.append({"moving_object": moving_name, "engaged_count": engaged})
                summary["minimum_engaged_count"] = engaged if summary["minimum_engaged_count"] is None else min(int(summary["minimum_engaged_count"]), engaged)
            summary["evaluated_pose_count"] += 1
            if group_failures:
                summary["failing_pose_ids"].append(pose_id)
                failures.append({"pose_id": pose_id, "contact_group_id": item["id"], "reasons": ["insufficient_engaged_candidates"], "details": group_failures})
    return {
        "document": document.Name,
        "manifest_id": str(manifest.get("id") or manifest.get("name") or "pose_sweep"),
        "pose_count": len(poses),
        "interface_count": len(interfaces),
        "contact_group_count": len(contact_groups),
        "valid": not failures,
        "failures": failures,
        "interfaces": list(summaries.values()),
        "contact_groups": list(group_summaries.values()),
        "warnings": ["Pose transforms were applied to temporary shape copies; document placements were not modified."],
    }


_CAD_COMMAND_HANDLERS = {
    'list_documents': _cmd_list_documents,
    'get_document_tree': _cmd_get_document_tree,
    'get_sketch_status': _cmd_get_sketch_status,
    'get_sketch_details': _cmd_get_sketch_details,
    'get_object_topology': _cmd_get_object_topology,
    'validate_pose_sweep': _cmd_validate_pose_sweep,
    'add_sketch_constraint': _cmd_add_sketch_constraint,
    'delete_sketch_constraint': _cmd_delete_sketch_constraint,
    'set_constraint_expression': _cmd_set_constraint_expression,
    'rename_sketch_constraint': _cmd_rename_sketch_constraint,
    'dimension_rectangle_sketch': _cmd_dimension_rectangle_sketch,
    'dimension_circle_sketch': _cmd_dimension_circle_sketch,
    'new_document': _cmd_new_document,
    'open_document': _cmd_open_document,
    'catalog_file_objects': _cmd_catalog_file_objects,
    'save_document': _cmd_save_document,
    'close_document': _cmd_close_document,
    'create_body': _cmd_create_body,
    'set_active_body': _cmd_set_active_body,
    'set_active_document': _cmd_set_active_document,
    'attach_sketch_to_face': _cmd_attach_sketch_to_face,
    'create_sketch_on_surface': _cmd_create_sketch_on_surface,
    'execute_code': _cmd_execute_code,
    'create_primitive': _cmd_create_primitive,
    'create_parametric_primitive': _cmd_create_parametric_primitive,
    'create_draft_array': _cmd_create_draft_array,
    'import_a2plus_part': _cmd_import_a2plus_part,
    'list_a2plus_importable_shapes': _cmd_list_a2plus_importable_shapes,
    'list_a2plus_parts': _cmd_list_a2plus_parts,
    'create_a2plus_constraint': _cmd_create_a2plus_constraint,
    'list_a2plus_constraints': _cmd_list_a2plus_constraints,
    'update_a2plus_constraint': _cmd_update_a2plus_constraint,
    'delete_a2plus_constraint': _cmd_delete_a2plus_constraint,
    'create_sketch': _cmd_create_sketch,
    'set_sketch_expression': _cmd_set_sketch_expression,
    'extrude_sketch': _cmd_extrude_sketch,
    'create_pad': _cmd_create_pad,
    'create_pocket': _cmd_create_pocket,
    'sweep_profile': _cmd_sweep_profile,
    'set_spreadsheet_cells': _cmd_set_spreadsheet_cells,
    'get_spreadsheet_cells': _cmd_get_spreadsheet_cells,
    'list_helpers': _cmd_list_helpers,
    'run_helper': _cmd_run_helper,
    'store_library_object': _cmd_store_library_object,
    'store_file_object_to_library': _cmd_store_file_object_to_library,
    'list_library_objects': _cmd_list_library_objects,
    'import_library_object': _cmd_import_library_object,
    'capture_snapshot': _cmd_capture_snapshot,
    'fit_view': _cmd_fit_view,
    'set_view_orientation': _cmd_set_view_orientation,
    'set_objects_visibility': _cmd_set_objects_visibility,
    'isolate_objects': _cmd_isolate_objects,
}

_CAD_AUTOFIT_COMMANDS = frozenset(['create_body', 'create_primitive', 'create_parametric_primitive', 'create_draft_array', 'create_sketch', 'create_sketch_on_surface', 'attach_sketch_to_face', 'add_sketch_constraint', 'delete_sketch_constraint', 'set_constraint_expression', 'dimension_rectangle_sketch', 'dimension_circle_sketch', 'set_sketch_expression', 'extrude_sketch', 'create_pad', 'create_pocket', 'sweep_profile', 'set_spreadsheet_cells', 'import_a2plus_part', 'create_a2plus_constraint', 'delete_a2plus_constraint', 'update_a2plus_constraint', 'import_library_object', 'run_helper'])


def _execute_command_with_cad_view(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    result = _execute_command_impl(command, params)
    if command not in _CAD_AUTOFIT_COMMANDS:
        return result
    App, Gui, _, _ = _require_freecad_modules()
    if Gui is None or not bool(getattr(App, "GuiUp", False)):
        return result
    try:
        document_name = params.get("document_name") or result.get("document")
        gui_document = Gui.getDocument(document_name) if document_name else Gui.activeDocument()
        if gui_document is not None:
            # fitAll centers the visible scene without changing orientation,
            # unhiding construction geometry, or switching the active document.
            view = gui_document.activeView()
            if view is not None:
                view.fitAll()
    except Exception as exc:
        # A presentation failure must not report a successful edit as failed:
        # clients might otherwise retry it and create duplicate geometry.
        console = getattr(App, "Console", None)
        if console is not None:
            console.PrintWarning(f"CAD view fit skipped after {command}: {exc}\n")
    return result



def _command_requires_gui_dispatch(command, params, Gui):
    if Gui is None:return False
    if command=='execute_code':return bool(params.get('gui_thread',True))
    return True


def start_bridge_server_from_config(path):
    """Start from an explicit configuration without importing server dependencies."""
    with Path(path).expanduser().open(encoding='utf8') as stream:
        config = json.load(stream)
    bridge = config.get('bridge') or {}
    return start_bridge_server(host=bridge.get('host','127.0.0.1'),
        port=int(bridge.get('port',48111)),token=bridge.get('token'))


class _BridgeHTTPServer(ThreadingHTTPServer):
    """Bound accepted workers, including clients stalled while sending headers."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._worker_slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(30.0)
        return request, address

    def process_request(self, request, address):
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._worker_slots.release()



def _bridge_runtime_host() -> str:
    return str(_bridge_state.get("host") or "127.0.0.1")



def _bridge_runtime_port() -> int:
    port = _bridge_state.get("port")
    return int(port if port is not None else 48111)



def _bridge_runtime_token() -> Optional[str]:
    token = _bridge_state.get("token")
    return None if token in {None, ""} else str(token)



def _bridge_server_socket_is_open(server) -> bool:
    sock = getattr(server, "socket", None)
    if sock is None:
        return False
    fileno = getattr(sock, "fileno", None)
    if not callable(fileno):
        return False
    try:
        return int(fileno()) >= 0
    except Exception:
        return False



def _clear_restart_error() -> None:
    _bridge_state["restart_error"] = None



def _cmd_restart_bridge_server(command, params, App, Gui, Part, Sketcher):
    return restart_bridge_server_deferred(
        host=params.get("host"),
        port=params.get("port"),
        token=params.get("token"),
        delay_seconds=float(params.get("delay_seconds", 0.1) or 0.0),
    )



def _discover_live_bridge_server():
    current_server = _bridge_state.get("server")
    thread = _bridge_state.get("thread")
    is_alive = getattr(thread, "is_alive", None)
    if current_server is not None and callable(is_alive) and not is_alive():
        # A failed serve_forever thread can leave the listening descriptor open.
        # Close just the listener; server_close may wait for unrelated RPC work.
        socket = getattr(current_server, 'socket', None)
        if socket is not None:
            socket.close()
    if current_server is not None and _bridge_server_socket_is_open(current_server):
        return current_server
    candidates = []
    for obj in gc.get_objects():
        try:
            if not isinstance(obj, ThreadingHTTPServer):
                continue
        except Exception:
            continue
        if not _bridge_server_socket_is_open(obj):
            continue
        handler = getattr(obj, "RequestHandlerClass", None)
        if handler is None:
            continue
        if getattr(handler, "__name__", "") != "_BridgeHandler":
            continue
        if str(getattr(handler, "__module__", "") or "") != __name__:
            continue
        candidates.append(obj)
    if not candidates:
        return None
    preferred_port = _bridge_state.get("port")
    if preferred_port is not None:
        for candidate in candidates:
            try:
                address = getattr(candidate, "server_address", None)
                if isinstance(address, tuple) and len(address) >= 2 and int(address[1]) == int(preferred_port):
                    return candidate
            except Exception:
                continue
    return candidates[0]



@_serialized_bridge_lifecycle
def _reconcile_bridge_state_from_runtime() -> bool:
    server = _discover_live_bridge_server()
    if server is None:
        _bridge_state["server"] = None
        _bridge_state["thread"] = None
        return False
    _bridge_state["server"] = server
    address = getattr(server, "server_address", None)
    if isinstance(address, tuple) and len(address) >= 2:
        _bridge_state["host"] = str(address[0])
        _bridge_state["port"] = int(address[1])
    token = getattr(server, "bridge_token", None)
    if token not in (None, "") and _bridge_state.get("token") in (None, ""):
        _bridge_state["token"] = token
    return True



@_serialized_bridge_lifecycle
def _restart_bridge_server_worker(host: str, port: int, token: Optional[str], delay_seconds: float) -> None:
    try:
        if delay_seconds > 0.0:
            time.sleep(delay_seconds)
        preserved_dispatcher = _bridge_state.get("dispatcher")
        stop_bridge_server(preserve_dispatcher=bool(preserved_dispatcher is not None))
        start_bridge_server(
            host=host,
            port=port,
            token=token,
            create_dispatcher=bool(preserved_dispatcher is None),
            dispatcher_override=preserved_dispatcher,
        )
        _clear_restart_error()
    except Exception as exc:
        _bridge_state["restart_error"] = str(exc)
    finally:
        _bridge_state["restart_pending"] = False
        _bridge_state["restart_thread"] = None



def restart_bridge_server_deferred(
    host: Optional[str] = None,
    port: Optional[int] = None,
    token: Optional[str] = None,
    delay_seconds: float = 0.1,
) -> Dict[str, Any]:
    with _bridge_state.setdefault("restart_lock", threading.Lock()):
        with _bridge_state.setdefault("lifecycle_lock", threading.RLock()):
            _reconcile_bridge_state_from_runtime()
        if bool(_bridge_state.get("restart_pending")):
            return {
                "restart_scheduled": True,
                "restart_pending": True,
                "host": _bridge_runtime_host(),
                "port": _bridge_runtime_port(),
            }
        resolved_host = str(host or _bridge_runtime_host())
        resolved_port = int(port if port is not None else _bridge_runtime_port())
        resolved_token = _bridge_runtime_token() if token is None else (None if token == "" else str(token))
        _bridge_state["restart_pending"] = True
        _clear_restart_error()
        thread = threading.Thread(
            target=_restart_bridge_server_worker,
            args=(resolved_host, resolved_port, resolved_token, float(delay_seconds)),
            daemon=True,
        )
        _bridge_state["restart_thread"] = thread
        thread.start()
        return {
            "restart_scheduled": True,
            "restart_pending": True,
            "host": resolved_host,
            "port": resolved_port,
            "delay_seconds": float(delay_seconds),
        }


_CAD_COMMAND_HANDLERS["restart_bridge_server"] = _cmd_restart_bridge_server
