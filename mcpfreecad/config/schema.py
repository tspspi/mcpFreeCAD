"""Pydantic models describing the mcpfreecad configuration."""

from __future__ import annotations

from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    level: str = "INFO"
    logfile: Optional[str] = None


class BridgeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 48111
    token: Optional[str] = None
    timeout_seconds: float = 30.0


class APIKeyConfig(BaseModel):
    id: str
    kdf: dict
    library_root: str
    allow_code_execution: bool = True
    allow_library_write: bool = True
    allow_snapshots: bool = True


class RemoteTransportConfig(BaseModel):
    uds: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None


class RemoteServerConfig(BaseModel):
    api_key_kdf: Optional[dict] = None
    uds: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    url_prefix: Optional[str] = None
    transport: Optional[RemoteTransportConfig] = None

    def resolved_endpoint(self) -> dict:
        transport = self.transport
        endpoint = {
            "uds": self.uds,
            "host": self.host,
            "port": self.port,
        }
        if transport is not None:
            endpoint["uds"] = endpoint["uds"] or transport.uds
            endpoint["host"] = endpoint["host"] or transport.host
            endpoint["port"] = endpoint["port"] or transport.port
        return endpoint


class StdIOConfig(BaseModel):
    library_root: str = str(Path.home() / "freecad-mcp-library")
    allow_code_execution: bool = True
    allow_library_write: bool = True
    allow_snapshots: bool = True


class Config(BaseModel):
    mode: Literal["stdio", "remotehttp"] = "stdio"
    stdio: StdIOConfig = Field(default_factory=StdIOConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    remote_server: Optional[RemoteServerConfig] = None
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)
    api_keys: List[APIKeyConfig] = Field(default_factory=list)
