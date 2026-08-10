import json

from mcpfreecad.freecad.bridge_server import (
    _a2plus_constraint_spec,
    _a2plus_constraint_by_name,
    _close_temp_document,
    _build_library_metadata,
    _open_document_for_inspection,
    _serialize_shape_topology,
    _parametric_primitive_spec,
    _read_library_metadata,
    _resolve_constraint_index,
    _serialize_sketch_constraint,
    _validate_library_metadata,
)


class FakeConstraint:
    Type = "DistanceX"
    Name = "Width"
    First = 0
    FirstPos = 1
    Second = 0
    SecondPos = 2
    Third = -2000
    ThirdPos = 0
    Value = 12.5


class FakeSketch:
    Name = "SketchA"
    ConstraintCount = 2
    Constraints = [
        type("C0", (), {"Name": "width"})(),
        type("C1", (), {"Name": "height"})(),
    ]


class FakeObject:
    Name = "Box01"
    Label = "Box 01"
    TypeId = "Part::Box"


class FakeVector:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class FakeSurface:
    def __init__(self, axis=None, position=None, radius=None):
        self.Axis = axis
        self.Position = position
        if radius is not None:
            self.Radius = radius


class FakeCurve:
    def __init__(self, axis=None, location=None, radius=None):
        self.Axis = axis
        self.Location = location
        if radius is not None:
            self.Radius = radius


class Plane:
    pass


class Circle:
    pass


class FakeFace:
    Area = 42.0
    CenterOfMass = FakeVector(1, 2, 3)
    Surface = Plane()


FakeFace.Surface.Axis = FakeVector(0, 0, 1)
FakeFace.Surface.Position = FakeVector(0, 0, 0)


class FakeEdge:
    Length = 12.5
    CenterOfMass = FakeVector(3, 2, 1)
    Curve = Circle()

    @staticmethod
    def isClosed():
        return True


FakeEdge.Curve.Axis = FakeVector(0, 1, 0)
FakeEdge.Curve.Location = FakeVector(5, 5, 0)
FakeEdge.Curve.Radius = 2.5


class FakeVertex:
    Point = FakeVector(9, 8, 7)


class FakeShape:
    Faces = [FakeFace()]
    Edges = [FakeEdge()]
    Vertexes = [FakeVertex()]


class FakeConstraintObject:
    def __init__(self, name="axisCoincident_001", content="ConstraintInfo"):
        self.Name = name
        self.Content = content


class FakeDocument:
    def __init__(self, obj):
        self._obj = obj

    def getObject(self, name):
        if self._obj is not None and self._obj.Name == name:
            return self._obj
        return None


class FakeOpenDoc:
    def __init__(self, name, filename):
        self.Name = name
        self.FileName = filename


class FakeAppForOpen:
    def __init__(self, existing=None, opened=None, active_name=None):
        self._existing = existing or []
        self._opened = opened
        self._active = type("AD", (), {"Name": active_name})() if active_name else None
        self.opened_paths = []
        self.set_active_calls = []
        self.closed = []

    def listDocuments(self):
        return {doc.Name: doc for doc in self._existing}

    @property
    def ActiveDocument(self):
        return self._active

    def openDocument(self, path):
        self.opened_paths.append(path)
        return self._opened

    def setActiveDocument(self, name):
        self.set_active_calls.append(name)

    def closeDocument(self, name):
        self.closed.append(name)


def test_parametric_primitive_spec_exposes_expected_property_map():
    type_id, property_map = _parametric_primitive_spec("box")

    assert type_id == "Part::Box"
    assert property_map == {
        "length": "Length",
        "width": "Width",
        "height": "Height",
    }


def test_serialize_sketch_constraint_captures_reference_fields():
    payload = _serialize_sketch_constraint(FakeConstraint())

    assert payload == {
        "type": "DistanceX",
        "name": "Width",
        "first": 0,
        "first_pos": 1,
        "second": 0,
        "second_pos": 2,
        "third": -2000,
        "third_pos": 0,
        "value": 12.5,
    }


def test_resolve_constraint_index_accepts_name_or_index():
    sketch = FakeSketch()

    assert _resolve_constraint_index(sketch, constraint_index=1) == 1
    assert _resolve_constraint_index(sketch, constraint_name="width") == 0


def test_library_metadata_roundtrip_and_validation(tmp_path):
    metadata = _build_library_metadata("entry-a", "DocA", FakeObject(), {"category": "fastener"})
    entry_dir = tmp_path / "entry-a"
    entry_dir.mkdir()
    (entry_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    validated = _read_library_metadata(entry_dir, "entry-a")

    assert validated["schema_version"] == 1
    assert validated["entry_id"] == "entry-a"
    assert validated["object"]["name"] == "Box01"
    assert validated["storage"] == {"format": "brep", "shape_file": "shape.brep"}
    assert validated["user_metadata"] == {"category": "fastener"}


def test_validate_library_metadata_rejects_missing_schema():
    try:
        _validate_library_metadata("entry-a", {"entry_id": "entry-a"})
        assert False, "expected validation error"
    except RuntimeError as exc:
        assert "schema_version" in str(exc)


def test_a2plus_constraint_spec_maps_supported_kind():
    normalized, class_name = _a2plus_constraint_spec("plane")

    assert normalized == "plane"
    assert class_name == "PlaneConstraint"


def test_serialize_shape_topology_reports_faces_edges_and_vertices():
    payload = _serialize_shape_topology(FakeShape(), include_vertices=True)

    assert payload["face_count"] == 1
    assert payload["edge_count"] == 1
    assert payload["vertex_count"] == 1
    assert payload["faces"][0]["name"] == "Face1"
    assert payload["faces"][0]["surface_type"] == "Plane"
    assert payload["edges"][0]["name"] == "Edge1"
    assert payload["edges"][0]["curve_type"] == "Circle"
    assert payload["vertices"][0]["name"] == "Vertex1"


def test_a2plus_constraint_by_name_accepts_constraint_info_objects():
    obj = FakeConstraintObject()

    resolved = _a2plus_constraint_by_name(FakeDocument(obj), "axisCoincident_001")

    assert resolved is obj


def test_a2plus_constraint_by_name_rejects_missing_or_wrong_type():
    try:
        _a2plus_constraint_by_name(FakeDocument(None), "missing")
        assert False, "expected missing constraint error"
    except RuntimeError as exc:
        assert "not found" in str(exc)

    try:
        _a2plus_constraint_by_name(FakeDocument(FakeConstraintObject(content="Part::Feature")) , "axisCoincident_001")
        assert False, "expected wrong type error"
    except RuntimeError as exc:
        assert "not an A2plus constraint" in str(exc)


def test_open_document_for_inspection_reuses_existing_document():
    existing = FakeOpenDoc("DocA", "/tmp/model.FCStd")
    app = FakeAppForOpen(existing=[existing], opened=None, active_name="ActiveDoc")

    document, should_close, normalized = _open_document_for_inspection(app, None, "/tmp/model.FCStd")

    assert document is existing
    assert should_close is False
    assert normalized == "/tmp/model.FCStd"
    assert app.opened_paths == []


def test_open_document_for_inspection_restores_previous_active_after_temp_open():
    opened = FakeOpenDoc("DocB", "/tmp/model.FCStd")
    app = FakeAppForOpen(existing=[], opened=opened, active_name="PrevDoc")

    document, should_close, normalized = _open_document_for_inspection(app, None, "/tmp/model.FCStd")

    assert document is opened
    assert should_close is True
    assert normalized == "/tmp/model.FCStd"
    assert app.opened_paths == ["/tmp/model.FCStd"]
    assert app.set_active_calls == ["PrevDoc"]


def test_close_temp_document_only_closes_when_requested():
    opened = FakeOpenDoc("DocB", "/tmp/model.FCStd")
    app = FakeAppForOpen()

    _close_temp_document(app, opened, True)
    _close_temp_document(app, opened, False)

    assert app.closed == ["DocB"]
