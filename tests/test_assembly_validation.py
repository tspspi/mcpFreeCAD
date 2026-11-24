"""Assembly validation and A2plus regression coverage."""
from types import SimpleNamespace
from contextlib import nullcontext, contextmanager
import sys
import types
import pytest
from mcpfreecad.freecad import bridge_server

def test_pose_sweep_uses_temporary_shapes_and_reports_contact_and_clearance_failures(monkeypatch):
    class Vector:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = float(x), float(y), float(z)

        @property
        def Length(self):
            return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

    class Shape:
        rotations = []

        def __init__(self, x):
            self.x = float(x)

        def copy(self):
            return Shape(self.x)

        def translate(self, vector):
            self.x += vector.x

        def rotate(self, origin, axis, angle):
            self.rotations.append((origin.x, origin.y, origin.z, axis.x, axis.y, axis.z, angle))
            return None

        def distToShape(self, other):
            return (abs(self.x - other.x), [], [])

        def common(self, other):
            return SimpleNamespace(Volume=0.0)

        def isNull(self):
            return False

    app = SimpleNamespace(Vector=Vector)
    moving = SimpleNamespace(Name="Moving", Shape=Shape(0.0))
    fixed = SimpleNamespace(Name="Fixed", Shape=Shape(2.0))
    document = SimpleNamespace(Name="PoseDoc", getObject=lambda name: {"Moving": moving, "Fixed": fixed}.get(name))

    payload = bridge_server._validate_pose_sweep_payload(
        app,
        document,
        {
            "id": "test_sweep",
            "poses": [
                {"id": "neutral", "transforms": {}},
                {
                    "id": "engaged",
                    "transforms": {
                        "Moving": {
                            "translation": [2.0, 0.0, 0.0],
                            "rotation": {"axis": [0.0, 0.0, 1.0], "angle_deg": 0.0, "origin": [3.0, 0.0, 0.0]},
                        }
                    },
                },
            ],
            "interfaces": [
                {"id": "drive_contact", "objects": ["Moving", "Fixed"], "kind": "allowed_contact", "max_clearance_mm": 0.1},
                {"id": "keep_clear", "objects": ["Moving", "Fixed"], "kind": "forbidden_clearance", "min_clearance_mm": 0.5},
            ],
            "contact_groups": [
                {
                    "id": "ring_candidates",
                    "moving_objects": ["Moving"],
                    "candidate_objects": ["Fixed"],
                    "min_engaged_per_moving_object": 1,
                    "max_clearance_mm": 0.1,
                },
            ],
        },
    )

    assert payload["valid"] is False
    assert moving.Shape.x == 0.0
    assert fixed.Shape.x == 2.0
    assert Shape.rotations == [(3.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)]
    by_id = {item["interface"]["id"]: item for item in payload["interfaces"]}
    assert by_id["drive_contact"]["failing_pose_ids"] == ["neutral"]
    assert by_id["keep_clear"]["failing_pose_ids"] == ["engaged"]
    assert payload["contact_groups"][0]["failing_pose_ids"] == ["neutral"]


def test_pose_sweep_contained_in_requires_the_full_moving_shape_to_stay_in_cavity():
    class Vector:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = float(x), float(y), float(z)

        @property
        def Length(self):
            return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

    class Shape:
        def __init__(self, x, volume=1.0, contained_until=0.5):
            self.x = float(x)
            self.Volume = float(volume)
            self.contained_until = float(contained_until)

        def copy(self):
            return Shape(self.x, self.Volume, self.contained_until)

        def translate(self, vector):
            self.x += vector.x

        def rotate(self, origin, axis, angle):
            return None

        def distToShape(self, other):
            return (abs(self.x - other.x), [], [])

        def common(self, other):
            return SimpleNamespace(Volume=self.Volume if abs(self.x - other.x) <= self.contained_until else 0.0)

        def isNull(self):
            return False

    app = SimpleNamespace(Vector=Vector)
    pin = SimpleNamespace(Name="Pin", Shape=Shape(0.0))
    cavity = SimpleNamespace(Name="Cavity", Shape=Shape(0.0, volume=4.0))
    document = SimpleNamespace(Name="ContainmentDoc", getObject=lambda name: {"Pin": pin, "Cavity": cavity}.get(name))

    payload = bridge_server._validate_pose_sweep_payload(
        app,
        document,
        {
            "poses": [
                {"id": "inside", "transforms": {}},
                {"id": "escaped", "transforms": {"Pin": {"translation": [1.0, 0.0, 0.0]}}},
            ],
            "interfaces": [{"id": "pin_in_hole", "objects": ["Pin", "Cavity"], "kind": "contained_in"}],
        },
    )

    assert payload["valid"] is False
    interface = payload["interfaces"][0]
    assert interface["minimum_containment_ratio"] == 0.0
    assert interface["failing_pose_ids"] == ["escaped"]
    assert payload["failures"][0]["reasons"] == ["contained_body_escapes_cavity"]


def test_list_a2plus_importable_shapes_helper_returns_sorted_labels_and_restores_active_document(monkeypatch):
    main_doc = SimpleNamespace(Name="AssemblyDoc")
    import_doc = SimpleNamespace(Name="SourceDoc", Label="MotorSource", FileName="/tmp/motor.FCStd")
    active_name = {"value": "AssemblyDoc"}

    app = SimpleNamespace(
        ActiveDocument=SimpleNamespace(Name="AssemblyDoc"),
        setActiveDocument=lambda name: active_name.__setitem__("value", name),
    )

    monkeypatch.setattr(bridge_server, "_ensure_a2plus_support", lambda: None)
    monkeypatch.setattr(
        bridge_server,
        "_open_a2plus_source_document",
        lambda FreeCAD, source_path: (import_doc, True, "/tmp/motor.FCStd"),
    )
    monkeypatch.setattr(
        bridge_server,
        "_require_freecad_modules",
        lambda: (app, object(), object(), object()),
    )
    monkeypatch.setattr(
        bridge_server,
        "_temporary_a2plus_import_invisible_shapes_disabled",
        contextmanager(lambda: iter((None,))),
    )

    class _TopoMapper:
        def __init__(self, document):
            assert document is import_doc

        def getTopLevelObjects(self, allowSketches=True):
            assert allowSketches is True
            return [
                SimpleNamespace(Name="BodyB", Label="Z Body", TypeId="Part::Feature", ViewObject=SimpleNamespace(Visibility=False)),
                SimpleNamespace(Name="BodyA", Label="A Body", TypeId="PartDesign::Body", ViewObject=SimpleNamespace(Visibility=True)),
            ]

    closed = []
    app.closeDocument = lambda name: closed.append(name)

    import types
    monkeypatch.setitem(sys.modules, "a2p_importpart", types.SimpleNamespace(TopoMapper=_TopoMapper))

    payload = bridge_server._list_a2plus_importable_shapes(app, "/tmp/motor.FCStd")

    assert payload["path"] == "/tmp/motor.FCStd"
    assert payload["source_document"] == "SourceDoc"
    assert payload["source_document_label"] == "MotorSource"
    assert payload["reused_open_document"] is False
    assert payload["shape_count"] == 2
    assert payload["shapes"] == [
        {"object_name": "BodyA", "label": "A Body", "type_id": "PartDesign::Body", "is_visible": True},
        {"object_name": "BodyB", "label": "Z Body", "type_id": "Part::Feature", "is_visible": False},
    ]
    assert closed == ["SourceDoc"]
    assert active_name["value"] == "AssemblyDoc"


def test_list_a2plus_importable_shapes_command_wraps_payload(monkeypatch):
    app = SimpleNamespace(ActiveDocument=SimpleNamespace(Name="AssemblyDoc"))
    monkeypatch.setattr(bridge_server, "_require_freecad_modules", lambda: (app, object(), object(), object()))
    monkeypatch.setattr(
        bridge_server,
        "_list_a2plus_importable_shapes",
        lambda App, source_path: {"path": source_path, "shape_count": 1, "shapes": [{"label": "Body"}]},
    )

    payload = bridge_server._execute_command_impl("list_a2plus_importable_shapes", {"path": "/tmp/part.FCStd"})

    assert payload == {
        "path": "/tmp/part.FCStd",
        "shape_count": 1,
        "shapes": [{"label": "Body"}],
        "document": "AssemblyDoc",
    }


def test_patch_a2plus_solver_dialog_suppression_defaults_to_no_popup(monkeypatch):
    calls = []

    def _solve_constraints(*args, **kwargs):
        calls.append((args, dict(kwargs)))
        return True

    module = types.SimpleNamespace(solveConstraints=_solve_constraints)
    monkeypatch.setitem(sys.modules, "a2p_solversystem", module)

    bridge_server._patch_a2plus_solver_dialog_suppression()
    assert module._mcpfreecad_dialog_suppression_installed is True

    assert module.solveConstraints("doc") is True
    assert calls[-1][1]["showFailMessage"] is False

    assert module.solveConstraints("doc", False, None, True, True) is True
    assert "showFailMessage" not in calls[-1][1]

    assert module.solveConstraints("doc", showFailMessage=True) is True
    assert calls[-1][1]["showFailMessage"] is True


def test_patch_a2plus_solver_dialog_suppression_is_idempotent(monkeypatch):
    def _solve_constraints(*args, **kwargs):
        return kwargs

    module = types.SimpleNamespace(solveConstraints=_solve_constraints)
    monkeypatch.setitem(sys.modules, "a2p_solversystem", module)

    bridge_server._patch_a2plus_solver_dialog_suppression()
    wrapped = module.solveConstraints
    bridge_server._patch_a2plus_solver_dialog_suppression()

    assert module.solveConstraints is wrapped
