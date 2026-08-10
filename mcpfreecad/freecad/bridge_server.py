"""Bridge server intended to run inside FreeCAD's embedded Python."""

from __future__ import annotations

import json
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from mcpfreecad.workbenches import discover_helper_specs

_bridge_state = {
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
                    result_queue.put(("ok", _execute_command_impl(command, params)))
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
        "active_document": App.ActiveDocument.Name if App.ActiveDocument else None,
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


def _serialize_shape_topology(shape, *, include_vertices: bool = True) -> Dict[str, Any]:
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

    if command == "list_documents":
        docs = list(App.listDocuments().keys())
        active = App.ActiveDocument.Name if App.ActiveDocument else None
        return {"documents": docs, "active_document": active}

    if command == "get_document_tree":
        document = _active_document(App, params.get("document_name"))
        return _document_tree(
            document,
            include_properties=bool(params.get("include_properties", False)),
            property_names=params.get("property_names"),
            max_depth=int(params.get("max_depth", 4)),
            root_name=params.get("root_name"),
        )

    if command == "get_sketch_status":
        document, sketch = _sketch_by_name(App, params)
        return _sketch_status(sketch)

    if command == "get_sketch_details":
        document, sketch = _sketch_by_name(App, params)
        return _sketch_details(sketch)

    if command == "get_object_topology":
        document, obj = _object_by_name(App, params)
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            raise RuntimeError(f"Object '{obj.Name}' has no usable shape for topology inspection")
        return {
            "document": document.Name,
            "object": _object_summary(obj),
            **_serialize_shape_topology(shape, include_vertices=bool(params.get("include_vertices", True))),
        }

    if command == "add_sketch_constraint":
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

    if command == "delete_sketch_constraint":
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

    if command == "set_constraint_expression":
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

    if command == "rename_sketch_constraint":
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

    if command == "dimension_rectangle_sketch":
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

    if command == "dimension_circle_sketch":
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

    if command == "new_document":
        document = App.newDocument(params.get("name"))
        if params.get("activate", True):
            App.setActiveDocument(document.Name)
            _maybe_bind_gui_document(Gui, document.Name)
        return {"document": document.Name}

    if command == "open_document":
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

    if command == "catalog_file_objects":
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

    if command == "save_document":
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

    if command == "close_document":
        name = params["name"]
        document = App.getDocument(name)
        if document is None:
            raise RuntimeError(f"Document '{name}' not found")
        App.closeDocument(name)
        remaining_docs = list(App.listDocuments().keys())
        active = App.ActiveDocument.Name if App.ActiveDocument else None
        if active is not None:
            _maybe_bind_gui_document(Gui, active)
        return {
            "closed_document": name,
            "documents": remaining_docs,
            "active_document": active,
        }

    if command == "create_body":
        document = _active_document(App, params.get("document_name"))
        body = document.addObject("PartDesign::Body", params.get("name", "Body"))
        if params.get("activate", True):
            _set_active_body(App, Gui, document, body)
        document.recompute()
        return _object_summary(body)

    if command == "set_active_body":
        document = _active_document(App, params.get("document_name"))
        body = _resolve_body(document, params["body_name"])
        _set_active_body(App, Gui, document, body)
        return _object_summary(body)

    if command == "set_active_document":
        name = params["name"]
        document = App.getDocument(name)
        if document is None:
            raise RuntimeError(f"Document '{name}' not found")
        App.setActiveDocument(name)
        _maybe_bind_gui_document(Gui, name)
        return {"document": name}

    if command == "attach_sketch_to_face":
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

    if command == "create_sketch_on_surface":
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

    if command == "execute_code":
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

    if command == "create_primitive":
        document = _active_document(App, params.get("document_name"))
        kind = params["kind"].lower()
        shape_params = params.get("params") or {}
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

    if command == "create_parametric_primitive":
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

    if command == "create_draft_array":
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

    if command == "import_a2plus_part":
        if Gui is None:
            raise RuntimeError("FreeCADGui is not available, cannot import A2plus assembly parts")
        document = _active_document(App, params.get("document_name"))
        if not getattr(document, "FileName", ""):
            raise RuntimeError("A2plus import requires the target assembly document to be saved first")
        _ensure_a2plus_support()
        import a2p_importpart

        source_path = str(Path(params["path"]).expanduser())
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

    if command == "list_a2plus_parts":
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

    if command == "create_a2plus_constraint":
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

    if command == "list_a2plus_constraints":
        _ensure_a2plus_support()
        document = _active_document(App, params.get("document_name"))
        constraints = []
        for obj in document.Objects:
            if "ConstraintInfo" not in getattr(obj, "Content", ""):
                continue
            constraints.append(_serialize_a2plus_constraint(obj))
        constraints.sort(key=lambda entry: entry["name"])
        return {"document": document.Name, "constraints": constraints}

    if command == "update_a2plus_constraint":
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

    if command == "delete_a2plus_constraint":
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

    if command == "create_sketch":
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

    if command == "set_sketch_expression":
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

    if command == "extrude_sketch":
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

    if command == "create_pad":
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

    if command == "create_pocket":
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

    if command == "sweep_profile":
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

    if command == "set_spreadsheet_cells":
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

    if command == "get_spreadsheet_cells":
        document = _active_document(App, params.get("document_name"))
        sheet = document.getObject(params["name"])
        if sheet is None:
            raise RuntimeError(f"Spreadsheet '{params['name']}' not found")
        result = {}
        for cell in params.get("cells", []):
            result[cell] = sheet.get(cell)
        return {"sheet": sheet.Name, "cells": result}

    if command == "list_helpers":
        helpers = [spec.to_dict() for spec in _helper_registry().values()]
        helpers.sort(key=lambda entry: entry["name"])
        return {"helpers": helpers}

    if command == "run_helper":
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

    if command == "store_library_object":
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

    if command == "store_file_object_to_library":
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

    if command == "list_library_objects":
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

    if command == "import_library_object":
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

    if command == "capture_snapshot":
        if Gui is None:
            raise RuntimeError("FreeCADGui is not available, cannot capture snapshots")
        document = _active_document(App, params.get("document_name"))
        active_view = _active_view_for_document(Gui, document.Name)
        orientation = params.get("orientation")
        if orientation:
            _set_view_orientation(active_view, orientation)
        if params.get("fit_view", True):
            active_view.fitAll()
        target = params.get("filename")
        if not target:
            target = str((Path(params["library_root"]).expanduser() / "snapshots" / f"{document.Name}.png"))
        target_path = Path(target).expanduser()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        active_view.saveImage(str(target_path), int(params.get("width", 1600)), int(params.get("height", 1200)), "White")
        return {"path": str(target_path), "orientation": orientation, "fit_view": bool(params.get("fit_view", True))}

    if command == "fit_view":
        if Gui is None:
            raise RuntimeError("FreeCADGui is not available, cannot fit the active view")
        document = _active_document(App, params.get("document_name"))
        active_view = _active_view_for_document(Gui, document.Name)
        active_view.fitAll()
        return {"document": document.Name, "fit_view": True}

    if command == "set_view_orientation":
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

    if command == "set_objects_visibility":
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

    if command == "isolate_objects":
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

    raise RuntimeError(f"Unsupported command '{command}'")


def _execute_command(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    App, Gui, Part, Sketcher = _require_freecad_modules()
    del Part, Sketcher

    dispatcher = _bridge_state.get("dispatcher")
    if dispatcher is not None and bool(getattr(App, "GuiUp", False)) and Gui is not None:
        return dispatcher.call(command, params)
    return _execute_command_impl(command, params)


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


def start_bridge_server(host: str = "127.0.0.1", port: int = 48111, token: Optional[str] = None) -> Dict[str, Any]:
    if _bridge_state["server"] is not None:
        return {"running": True, "host": host, "port": port}
    try:
        App, Gui, Part, Sketcher = _require_freecad_modules()
        del Part, Sketcher
        if bool(getattr(App, "GuiUp", False)) and Gui is not None:
            _bridge_state["dispatcher"] = _BridgeDispatcher()
    except Exception:
        _bridge_state["dispatcher"] = None
    server = ThreadingHTTPServer((host, port), _BridgeHandler)
    server.bridge_token = token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _bridge_state["server"] = server
    _bridge_state["thread"] = thread
    _bridge_state["token"] = token
    return {"running": True, "host": host, "port": port}


def stop_bridge_server() -> Dict[str, Any]:
    server = _bridge_state["server"]
    if server is None:
        return {"running": False}
    server.shutdown()
    server.server_close()
    _bridge_state["server"] = None
    _bridge_state["thread"] = None
    _bridge_state["token"] = None
    _bridge_state["dispatcher"] = None
    return {"running": False}
