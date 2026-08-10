from mcpfreecad.freecad.bridge_server import _object_properties, _serialize_property_value


class QuantityLike:
    def __init__(self, text: str):
        self.Value = 1
        self._text = text

    def __str__(self):
        return self._text


class LinkedObject:
    Name = "Child"
    Label = "ChildLabel"
    TypeId = "Part::Feature"


class FakeObject:
    PropertiesList = ["Label", "Number", "Quantity", "Linked", "Broken"]
    Label = "Example"
    Number = 42
    Quantity = QuantityLike("2.0 mm")
    Linked = LinkedObject()

    @property
    def Broken(self):
        raise RuntimeError("cannot read")


def test_serialize_property_value_handles_supported_shapes():
    assert _serialize_property_value("abc") == "abc"
    assert _serialize_property_value(42) == 42
    assert _serialize_property_value([1, "x"]) == [1, "x"]
    assert _serialize_property_value({"a": 1}) == {"a": 1}
    assert _serialize_property_value(QuantityLike("5 mm")) == "5 mm"
    assert _serialize_property_value(LinkedObject()) == {
        "name": "Child",
        "label": "ChildLabel",
        "type_id": "Part::Feature",
    }


def test_object_properties_filters_and_captures_read_errors():
    props = _object_properties(FakeObject(), include_properties=True, property_names=["Label", "Broken"])

    assert props["Label"] == "Example"
    assert props["Broken"].startswith("<unreadable:")


def test_object_properties_can_be_disabled():
    assert _object_properties(FakeObject(), include_properties=False, property_names=None) == {}
