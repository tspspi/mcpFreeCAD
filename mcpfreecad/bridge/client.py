"""HTTP client for the FreeCAD in-process bridge."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from mcpfreecad.bridge.protocol import make_request
from mcpfreecad.config.schema import BridgeConfig


class BridgeError(RuntimeError):
    """Raised when the FreeCAD bridge returns an error."""


class FreeCADBridgeClient:
    def __init__(self, config: BridgeConfig):
        self._config = config

    @property
    def base_url(self) -> str:
        return f"http://{self._config.host}:{self._config.port}"

    def status(self) -> Dict[str, Any]:
        request = urllib.request.Request(f"{self.base_url}/status", method="GET")
        return self._json_request(request)

    def call(self, command: str, **params: Any) -> Dict[str, Any]:
        body = json.dumps(make_request(command, **params)).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/rpc",
            data=body,
            headers={
                "Content-Type": "application/json",
                **self._auth_headers(),
            },
            method="POST",
        )
        response = self._json_request(request)
        if not response.get("ok", False):
            raise BridgeError(response.get("error", "Unknown bridge error"))
        return response["result"]

    def _auth_headers(self) -> Dict[str, str]:
        if not self._config.token:
            return {}
        return {"Authorization": f"Bearer {self._config.token}"}

    def _json_request(self, request: urllib.request.Request) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BridgeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise BridgeError(f"Failed to reach FreeCAD bridge at {self.base_url}: {exc.reason}") from exc
