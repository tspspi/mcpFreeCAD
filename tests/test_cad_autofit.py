from types import SimpleNamespace

import pytest

from mcpfreecad.freecad import bridge_server


@pytest.fixture
def scene(monkeypatch):
    calls = []
    view = SimpleNamespace(fitAll=lambda: calls.append("fit"))
    document = SimpleNamespace(activeView=lambda: view)
    gui = SimpleNamespace(
        activeDocument=lambda: document,
        getDocument=lambda name: calls.append(name) or document,
    )
    app = SimpleNamespace(GuiUp=True, Console=SimpleNamespace(PrintWarning=calls.append))
    result = {"name": "Part"}
    monkeypatch.setattr(bridge_server, "_require_freecad_modules", lambda: (app, gui, None, None))
    monkeypatch.setattr(bridge_server, "_execute_command_impl",
                        lambda command, params: calls.append("edit") or result)
    return calls, app, gui, view, result


@pytest.mark.parametrize("command", sorted(bridge_server._CAD_AUTOFIT_COMMANDS))
def test_cad_edits_fit_once_after_success_and_require_gui_dispatch(scene, command):
    calls, _, gui, _, result = scene
    assert bridge_server._command_requires_gui_dispatch(command, {}, gui)
    assert bridge_server._execute_command_with_cad_view(command, {}) is result
    assert calls == ["edit", "fit"]


@pytest.mark.parametrize("command", ['set_view_orientation', 'fit_view', 'capture_snapshot', 'set_objects_visibility', 'isolate_objects', 'get_document_tree', 'get_object_topology', 'validate_pose_sweep', 'execute_code', 'set_active_document', 'open_document', 'save_document', 'rename_sketch_constraint'])
def test_other_commands_do_not_add_camera_changes(scene, command):
    calls, _, _, _, result = scene
    assert bridge_server._execute_command_with_cad_view(command, {}) is result
    assert calls == ["edit"]


def test_fits_target_document_not_unrelated_active_document(scene):
    calls, _, _, _, result = scene
    result["document"] = "ResultDocument"
    bridge_server._execute_command_with_cad_view("create_pad", {"document_name": "Target"})
    assert calls == ["edit", "Target", "fit"]
    calls.clear()
    bridge_server._execute_command_with_cad_view("create_pad", {})
    assert calls == ["edit", "ResultDocument", "fit"]


def test_failed_edit_does_not_fit(scene, monkeypatch):
    calls, *_ = scene

    def fail(*args):
        raise RuntimeError("model failed")

    monkeypatch.setattr(bridge_server, "_execute_command_impl", fail)
    with pytest.raises(RuntimeError, match="model failed"):
        bridge_server._execute_command_with_cad_view("create_pad", {})
    assert calls == []


def test_view_failure_does_not_fail_or_repeat_successful_edit(scene):
    calls, _, _, view, result = scene

    def fail():
        raise RuntimeError("view closed")

    view.fitAll = fail
    assert bridge_server._execute_command_with_cad_view("create_pad", {}) is result
    assert calls[0] == "edit"
    assert len(calls) == 2
    assert "view closed" in calls[1]


def test_headless_edits_do_not_touch_gui(scene):
    calls, app, _, _, _ = scene
    app.GuiUp = False
    bridge_server._execute_command_with_cad_view("create_pad", {})
    assert calls == ["edit"]


def test_closed_gui_document_is_ignored(scene):
    calls, _, gui, _, _ = scene
    gui.activeDocument = lambda: None
    bridge_server._execute_command_with_cad_view("create_pad", {})
    assert calls == ["edit"]
