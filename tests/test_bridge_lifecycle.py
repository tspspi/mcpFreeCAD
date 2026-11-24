"""Bridge lifecycle recovery and deferred-restart regression tests."""
from types import SimpleNamespace
import types
import threading
import pytest
from mcpfreecad.freecad import bridge_server

def test_restart_bridge_server_deferred_schedules_background_worker(monkeypatch):
    scheduled = {}

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            scheduled["target"] = target
            scheduled["args"] = args
            scheduled["daemon"] = daemon

        def start(self):
            scheduled["started"] = True

    monkeypatch.setattr(bridge_server.threading, "Thread", _FakeThread)
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": None,
            "thread": None,
            "token": "bridge-token",
            "dispatcher": None,
            "host": "127.0.0.1",
            "port": 48111,
            "restart_pending": False,
            "restart_error": "old error",
            "restart_thread": None,
            "restart_lock": bridge_server.threading.Lock(),
        },
    )

    payload = bridge_server.restart_bridge_server_deferred(delay_seconds=0.25)

    assert payload == {
        "restart_scheduled": True,
        "restart_pending": True,
        "host": "127.0.0.1",
        "port": 48111,
        "delay_seconds": 0.25,
    }
    assert bridge_server._bridge_state["restart_pending"] is True
    assert bridge_server._bridge_state["restart_error"] is None
    assert scheduled["target"] is bridge_server._restart_bridge_server_worker
    assert scheduled["args"] == ("127.0.0.1", 48111, "bridge-token", 0.25)
    assert scheduled["daemon"] is True
    assert scheduled["started"] is True


def test_start_bridge_server_backfills_missing_runtime_fields_for_existing_server(monkeypatch):
    server = SimpleNamespace(
        server_address=("127.0.0.1", 48111), bridge_token="bridge-token",
        socket=SimpleNamespace(fileno=lambda: 3),
    )
    def unexpected_construction(*args, **kwargs):
        raise AssertionError("Existing listener must not construct another server or FreeCAD dispatcher")
    monkeypatch.setattr(bridge_server, "_BridgeHTTPServer", unexpected_construction)
    monkeypatch.setattr(bridge_server, "_require_freecad_modules", unexpected_construction)
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": server,
            "thread": None,
            "token": None,
            "dispatcher": None,
            "host": None,
            "port": None,
            "restart_pending": False,
            "restart_error": None,
            "restart_thread": None,
            "restart_lock": bridge_server.threading.Lock(),
        },
    )

    payload = bridge_server.start_bridge_server(host="127.0.0.1", port=48111, token="bridge-token")

    assert payload == {"running": True, "host": "127.0.0.1", "port": 48111}
    assert bridge_server._bridge_state["host"] == "127.0.0.1"
    assert bridge_server._bridge_state["port"] == 48111
    assert bridge_server._bridge_state["token"] == "bridge-token"


def test_start_bridge_server_discovers_live_server_before_rebinding(monkeypatch):
    class _FakeServer:
        def __init__(self, *args):
            if args:
                bind_attempted["value"] = True
                raise AssertionError("start_bridge_server attempted to bind a second HTTP server")
            self.server_address = ("127.0.0.1", 48111)
            self.bridge_token = "bridge-token"
            self.RequestHandlerClass = type(
                "_BridgeHandler",
                (),
                {"__module__": "mcpfreecad.freecad.bridge_server"},
            )

    fake_server = _FakeServer()
    bind_attempted = {"value": False}

    monkeypatch.setattr(bridge_server, "ThreadingHTTPServer", _FakeServer)
    monkeypatch.setattr(bridge_server.gc, "get_objects", lambda: [fake_server])
    monkeypatch.setattr(bridge_server, "_bridge_server_socket_is_open", lambda server: server is fake_server)
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": None,
            "thread": None,
            "token": None,
            "dispatcher": None,
            "host": None,
            "port": None,
            "restart_pending": False,
            "restart_error": None,
            "restart_thread": None,
            "restart_lock": bridge_server.threading.Lock(),
        },
    )

    monkeypatch.setattr(bridge_server, "_require_freecad_modules", lambda: (SimpleNamespace(GuiUp=False), None, None, None))

    payload = bridge_server.start_bridge_server(host="127.0.0.1", port=48111, token="bridge-token")

    assert payload == {"running": True, "host": "127.0.0.1", "port": 48111}
    assert bind_attempted["value"] is False
    assert bridge_server._bridge_state["server"] is fake_server
    assert bridge_server._bridge_state["host"] == "127.0.0.1"
    assert bridge_server._bridge_state["port"] == 48111
    assert bridge_server._bridge_state["token"] == "bridge-token"


def test_stop_bridge_server_discovers_live_server_when_state_missing(monkeypatch):
    closed = []

    class _FakeServer:
        def __init__(self):
            self.server_address = ("127.0.0.1", 48111)
            self.bridge_token = "bridge-token"
            self.RequestHandlerClass = type(
                "_BridgeHandler",
                (),
                {"__module__": "mcpfreecad.freecad.bridge_server"},
            )

        def shutdown(self):
            closed.append("shutdown")

        def server_close(self):
            closed.append("server_close")

    fake_server = _FakeServer()
    monkeypatch.setattr(bridge_server, "ThreadingHTTPServer", _FakeServer)
    monkeypatch.setattr(bridge_server.gc, "get_objects", lambda: [fake_server])
    monkeypatch.setattr(bridge_server, "_bridge_server_socket_is_open", lambda server: server is fake_server)
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": None,
            "thread": None,
            "token": None,
            "dispatcher": "dispatcher",
            "host": None,
            "port": None,
            "restart_pending": False,
            "restart_error": None,
            "restart_thread": None,
            "restart_lock": bridge_server.threading.Lock(),
        },
    )

    payload = bridge_server.stop_bridge_server()

    assert payload == {"running": False}
    assert closed == ["shutdown", "server_close"]
    assert bridge_server._bridge_state["server"] is None
    assert bridge_server._bridge_state["thread"] is None
    assert bridge_server._bridge_state["token"] is None
    assert bridge_server._bridge_state["dispatcher"] is None


def test_restart_bridge_server_deferred_returns_existing_pending_restart(monkeypatch):
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": None,
            "thread": None,
            "token": "bridge-token",
            "dispatcher": None,
            "host": "127.0.0.1",
            "port": 48111,
            "restart_pending": True,
            "restart_error": None,
            "restart_thread": object(),
            "restart_lock": bridge_server.threading.Lock(),
        },
    )

    payload = bridge_server.restart_bridge_server_deferred()

    assert payload == {
        "restart_scheduled": True,
        "restart_pending": True,
        "host": "127.0.0.1",
        "port": 48111,
    }


def test_restart_bridge_server_worker_stops_then_starts_and_clears_state(monkeypatch):
    calls = []
    dispatcher = object()
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": object(),
            "thread": object(),
            "token": "bridge-token",
            "dispatcher": dispatcher,
            "host": "127.0.0.1",
            "port": 48111,
            "restart_pending": True,
            "restart_error": "old error",
            "restart_thread": object(),
            "restart_lock": bridge_server.threading.Lock(),
        },
    )
    monkeypatch.setattr(bridge_server.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))
    monkeypatch.setattr(
        bridge_server,
        "stop_bridge_server",
        lambda preserve_dispatcher=False: calls.append(("stop", preserve_dispatcher)) or {"running": False},
    )
    monkeypatch.setattr(
        bridge_server,
        "start_bridge_server",
        lambda host, port, token, create_dispatcher=True, dispatcher_override=None: calls.append(
            ("start", (host, port, token, create_dispatcher, dispatcher_override))
        )
        or {"running": True},
    )

    bridge_server._restart_bridge_server_worker("127.0.0.1", 48111, "bridge-token", 0.2)

    assert calls == [
        ("sleep", 0.2),
        ("stop", True),
        ("start", ("127.0.0.1", 48111, "bridge-token", False, dispatcher)),
    ]
    assert bridge_server._bridge_state["restart_pending"] is False
    assert bridge_server._bridge_state["restart_thread"] is None
    assert bridge_server._bridge_state["restart_error"] is None


def test_restart_bridge_server_worker_records_error_and_clears_pending(monkeypatch):
    dispatcher = object()
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": object(),
            "thread": object(),
            "token": "bridge-token",
            "dispatcher": dispatcher,
            "host": "127.0.0.1",
            "port": 48111,
            "restart_pending": True,
            "restart_error": None,
            "restart_thread": object(),
            "restart_lock": bridge_server.threading.Lock(),
        },
    )
    monkeypatch.setattr(bridge_server.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        bridge_server,
        "stop_bridge_server",
        lambda preserve_dispatcher=False: (_ for _ in ()).throw(RuntimeError("bind failed")),
    )

    bridge_server._restart_bridge_server_worker("127.0.0.1", 48111, "bridge-token", 0.0)

    assert bridge_server._bridge_state["restart_pending"] is False
    assert bridge_server._bridge_state["restart_thread"] is None
    assert "bind failed" in bridge_server._bridge_state["restart_error"]


def test_start_bridge_server_can_reuse_existing_dispatcher_without_gui_construction(monkeypatch):
    created_server = []

    class _FakeServer:
        def __init__(self, address, handler_class):
            self.server_address = address
            self.RequestHandlerClass = handler_class
            self.bridge_token = None
            self.socket = SimpleNamespace(fileno=lambda: 3)
            created_server.append((address, handler_class))

        def serve_forever(self):
            return None

    thread_events = []

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            thread_events.append(("start", self.target, self.daemon))

    dispatcher = object()
    monkeypatch.setattr(bridge_server.gc, "get_objects", lambda: [])
    monkeypatch.setattr(bridge_server, "_BridgeHTTPServer", _FakeServer)
    monkeypatch.setattr(bridge_server.threading, "Thread", _FakeThread)
    monkeypatch.setattr(
        bridge_server,
        "_require_freecad_modules",
        lambda: (_ for _ in ()).throw(AssertionError("GUI module construction should not run when reusing a dispatcher")),
    )
    monkeypatch.setattr(
        bridge_server,
        "_bridge_state",
        {
            "server": None,
            "thread": None,
            "token": None,
            "dispatcher": None,
            "host": None,
            "port": None,
            "restart_pending": False,
            "restart_error": None,
            "restart_thread": None,
            "restart_lock": bridge_server.threading.Lock(),
        },
    )

    payload = bridge_server.start_bridge_server(
        host="127.0.0.1",
        port=48111,
        token="bridge-token",
        create_dispatcher=False,
        dispatcher_override=dispatcher,
    )

    assert payload == {"running": True, "host": "127.0.0.1", "port": 48111}
    assert created_server == [(("127.0.0.1", 48111), bridge_server._BridgeHandler)]
    assert thread_events == [("start", bridge_server._bridge_state["server"].serve_forever, True)]
    assert bridge_server._bridge_state["dispatcher"] is dispatcher
    assert bridge_server._bridge_state["token"] == "bridge-token"


def test_execute_command_impl_restart_bridge_server_delegates(monkeypatch):
    monkeypatch.setattr(bridge_server, "_require_freecad_modules", lambda: (object(), None, object(), object()))
    monkeypatch.setattr(
        bridge_server,
        "restart_bridge_server_deferred",
        lambda host=None, port=None, token=None, delay_seconds=0.1: {
            "restart_scheduled": True,
            "host": host,
            "port": port,
            "token": token,
            "delay_seconds": delay_seconds,
        },
    )

    payload = bridge_server._execute_command_impl(
        "restart_bridge_server",
        {"host": "127.0.0.1", "port": 48112, "token": "next-token", "delay_seconds": 0.5},
    )

    assert payload == {
        "restart_scheduled": True,
        "host": "127.0.0.1",
        "port": 48112,
        "token": "next-token",
        "delay_seconds": 0.5,
    }




def test_restart_accepts_state_from_an_older_loaded_bridge(monkeypatch):
    state=dict(server=object(),thread=None,dispatcher=object(),host='127.0.0.1',port=48111,token='test')
    monkeypatch.setattr(bridge_server,'_bridge_state',state)
    monkeypatch.setattr(bridge_server,'_reconcile_bridge_state_from_runtime',lambda:True)
    class Thread:
        def __init__(self,**kwargs):self.kwargs=kwargs
        def start(self):pass
    monkeypatch.setattr(bridge_server.threading,'Thread',Thread)
    result=bridge_server.restart_bridge_server_deferred()
    assert result['restart_pending'] and state['restart_lock'] is not None
