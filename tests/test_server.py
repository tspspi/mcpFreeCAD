from pathlib import Path

import pytest

from mcpfreecad.app.server import (
    SNAPSHOT_ROUTE_PATH,
    _a2plus_supported,
    _build_fastmcp_server,
    _build_remote_fastapi_app,
    _build_stdio_key_config,
    _extract_api_token,
    _get_snapshot_record,
    _resolve_snapshot_file,
    _resolve_snapshot_file_by_token,
    _snapshot_download_url,
    _snapshot_base64_payload,
    _store_snapshot_record,
)
from mcpfreecad.config.schema import APIKeyConfig, Config, RemoteServerConfig


class DummyRequest:
    def __init__(self, headers=None, query_params=None):
        self.headers = headers or {}
        self.query_params = query_params or {}


def test_build_stdio_key_config(tmp_path):
    config = Config()
    config.stdio.library_root = str(tmp_path / "library")
    key = _build_stdio_key_config(config)
    assert Path(key.library_root) == tmp_path / "library"
    assert (tmp_path / "library").exists()


def test_extract_api_token_prefers_bearer():
    request = DummyRequest(
        headers={"Authorization": "Bearer secret", "x-api-key": "header"},
        query_params={"mcp": "query", "api_key": "legacy"},
    )
    assert _extract_api_token(request, "mcp", "api_key") == "secret"


def test_extract_api_token_uses_mcp_query():
    request = DummyRequest(headers={}, query_params={"mcp": "query", "api_key": "legacy"})
    assert _extract_api_token(request, "mcp", "api_key") == "query"


def test_a2plus_supported_when_module_importable(monkeypatch):
    monkeypatch.setattr("mcpfreecad.app.server.importlib.util.find_spec", lambda name: object() if name == "a2plib" else None)
    assert _a2plus_supported() is True


def test_store_snapshot_record_normalizes_path(tmp_path):
    config = Config()
    _, state = _build_fastmcp_server(config)
    image_path = tmp_path / "snapshots" / "probe.png"
    record = _store_snapshot_record(state, str(image_path), "cad-key")

    assert record.snapshot_id
    assert record.path == str(image_path)
    assert record.key_id == "cad-key"
    assert record.access_token
    assert _get_snapshot_record(state, record.snapshot_id) == record


def test_snapshot_base64_payload_returns_png_data(tmp_path):
    config = Config()
    _, state = _build_fastmcp_server(config)
    snapshot_path = tmp_path / "snapshots" / "capture.png"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"pngdata")
    record = _store_snapshot_record(state, str(snapshot_path), "cad-key")

    payload = _snapshot_base64_payload(state, record.snapshot_id, "cad-key")

    assert payload["snapshot_id"] == record.snapshot_id
    assert payload["filename"] == "capture.png"
    assert payload["media_type"] == "image/png"
    assert payload["base64"] == "cG5nZGF0YQ=="


def test_snapshot_download_url_uses_relative_fallback():
    config = Config()

    assert _snapshot_download_url(config, "abc123") == "../snapshots/abc123"
    assert _snapshot_download_url(config, "abc123", access_token="tok") == "../snapshots/abc123?token=tok"


def test_snapshot_download_url_uses_configured_url_prefix():
    config = Config(
        remote_server=RemoteServerConfig(
            host="127.0.0.1",
            port=18080,
            url_prefix="https://mcp.example.com/freecad/",
        )
    )

    assert _snapshot_download_url(config, "abc123") == "https://mcp.example.com/freecad/snapshots/abc123"
    assert (
        _snapshot_download_url(config, "abc123", access_token="tok")
        == "https://mcp.example.com/freecad/snapshots/abc123?token=tok"
    )


def test_resolve_snapshot_file_rejects_other_key(tmp_path):
    config = Config()
    _, state = _build_fastmcp_server(config)
    snapshot_path = tmp_path / "snapshots" / "capture.png"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"pngdata")
    record = _store_snapshot_record(state, str(snapshot_path), "key-a")

    with pytest.raises(RuntimeError, match="Snapshot not found"):
        _resolve_snapshot_file(state, record.snapshot_id, "key-b")


def test_resolve_snapshot_file_by_token_accepts_matching_token(tmp_path):
    config = Config()
    _, state = _build_fastmcp_server(config)
    snapshot_path = tmp_path / "snapshots" / "capture.png"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"pngdata")
    record = _store_snapshot_record(state, str(snapshot_path), "key-a")

    resolved = _resolve_snapshot_file_by_token(state, record.snapshot_id, record.access_token)

    assert resolved == snapshot_path


def test_snapshot_download_requires_matching_key(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    del fastapi
    testclient = pytest.importorskip("fastapi.testclient")
    TestClient = testclient.TestClient

    config = Config(
        remote_server=RemoteServerConfig(host="127.0.0.1", port=18080),
        api_keys=[
            APIKeyConfig(id="key-a", kdf={}, library_root=str(tmp_path / "lib-a")),
            APIKeyConfig(id="key-b", kdf={}, library_root=str(tmp_path / "lib-b")),
        ],
    )
    monkeypatch.setattr(
        "mcpfreecad.app.server.match_api_key",
        lambda token, key_configs: next((key for key in key_configs if key.id == token), None),
    )

    server, state = _build_fastmcp_server(config)
    app = _build_remote_fastapi_app(server, state)

    snapshot_path = tmp_path / "snapshots" / "capture.png"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"pngdata")
    record = _store_snapshot_record(state, str(snapshot_path), "key-a")

    client = TestClient(app)

    missing = client.get(f"{SNAPSHOT_ROUTE_PATH}/missing", headers={"x-api-key": "key-a"})
    assert missing.status_code == 404

    wrong_key = client.get(f"{SNAPSHOT_ROUTE_PATH}/{record.snapshot_id}", headers={"x-api-key": "key-b"})
    assert wrong_key.status_code == 404


def test_snapshot_download_serves_registered_file(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    del fastapi
    testclient = pytest.importorskip("fastapi.testclient")
    TestClient = testclient.TestClient

    config = Config(
        remote_server=RemoteServerConfig(host="127.0.0.1", port=18080),
        api_keys=[APIKeyConfig(id="key-a", kdf={}, library_root=str(tmp_path / "lib-a"))],
    )
    monkeypatch.setattr(
        "mcpfreecad.app.server.match_api_key",
        lambda token, key_configs: key_configs[0] if token == "secret-a" else None,
    )

    server, state = _build_fastmcp_server(config)
    app = _build_remote_fastapi_app(server, state)

    snapshot_path = tmp_path / "snapshots" / "capture.png"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"pngdata")
    record = _store_snapshot_record(state, str(snapshot_path), "key-a")

    client = TestClient(app)
    response = client.get(f"{SNAPSHOT_ROUTE_PATH}/{record.snapshot_id}", headers={"x-api-key": "secret-a"})

    assert response.status_code == 200
    assert response.content == b"pngdata"


def test_snapshot_download_serves_registered_file_by_token(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    del fastapi
    testclient = pytest.importorskip("fastapi.testclient")
    TestClient = testclient.TestClient

    config = Config(
        remote_server=RemoteServerConfig(host="127.0.0.1", port=18080),
        api_keys=[APIKeyConfig(id="key-a", kdf={}, library_root=str(tmp_path / "lib-a"))],
    )
    monkeypatch.setattr(
        "mcpfreecad.app.server.match_api_key",
        lambda token, key_configs: key_configs[0] if token == "secret-a" else None,
    )

    server, state = _build_fastmcp_server(config)
    app = _build_remote_fastapi_app(server, state)

    snapshot_path = tmp_path / "snapshots" / "capture.png"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"pngdata")
    record = _store_snapshot_record(state, str(snapshot_path), "key-a")

    client = TestClient(app)
    response = client.get(f"{SNAPSHOT_ROUTE_PATH}/{record.snapshot_id}?token={record.access_token}")

    assert response.status_code == 200
    assert response.content == b"pngdata"
