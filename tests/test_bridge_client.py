import json
import pytest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcpfreecad.bridge.client import BridgeError, FreeCADBridgeClient
from mcpfreecad.config.schema import BridgeConfig


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/status":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"running": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        if self.headers.get("Authorization") != "Bearer bridge-token":
            payload = json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps({"ok": True, "result": {"echo": body["command"]}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def test_bridge_client_roundtrip():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = FreeCADBridgeClient(
            BridgeConfig(host="127.0.0.1", port=server.server_address[1], token="bridge-token", timeout_seconds=2.0)
        )
        assert client.status() == {"running": True}
        assert client.call("ping") == {"echo": "ping"}
    finally:
        server.shutdown()
        server.server_close()


def test_bridge_client_auth_failure():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = FreeCADBridgeClient(
            BridgeConfig(host="127.0.0.1", port=server.server_address[1], token="wrong", timeout_seconds=2.0)
        )
        try:
            client.call("ping")
            assert False, "expected BridgeError"
        except BridgeError as exc:
            assert "401" in str(exc)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("failure", [TimeoutError("stalled body"),
    ConnectionResetError("closed body"), b"not JSON", b"\xff", b"[]"])
def test_response_body_failures_are_bridge_errors(monkeypatch, failure):
    from contextlib import contextmanager
    from types import SimpleNamespace
    @contextmanager
    def opened(*args, **kwargs):
        def read():
            if isinstance(failure, Exception):
                raise failure
            return failure
        yield SimpleNamespace(read=read)
    monkeypatch.setattr("urllib.request.urlopen", opened)
    with pytest.raises(BridgeError):
        FreeCADBridgeClient(BridgeConfig()).status()
