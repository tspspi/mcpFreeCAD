"""Configuration loading and CLI parsing for mcpfreecad."""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Dict, Optional

from pydantic import ValidationError

from mcpfreecad.config.api_keys import derive_argon2id_hash, ensure_kdf_defaults, generate_random_api_key
from mcpfreecad.config.schema import Config, LoggingConfig

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/mcpfreecad.conf")
_cached_config: Optional[Config] = None


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP FreeCAD Server")
    parser.add_argument("--config", type=str, help="Path to configuration file", default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override logging level",
    )
    parser.add_argument("--logfile", type=str, default=None, help="Override log file path")
    parser.add_argument(
        "--transport",
        type=str,
        default="stdio",
        choices=["stdio", "remotehttp"],
        help="Transport mode (stdio or remotehttp)",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Shortcut to launch in remote HTTP mode (equivalent to --transport remotehttp)",
    )
    parser.add_argument("--genkey", type=str, metavar="ID", help="Generate/rotate API key for the given id")
    args = parser.parse_args()
    if getattr(args, "remote", False):
        args.transport = "remotehttp"
    return args


def load_config(path: Optional[str] = None) -> Config:
    config_path = path or DEFAULT_CONFIG_PATH
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Configuration file not found at {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in configuration file {config_path}: {exc}") from exc

    try:
        return Config(**data)
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration: {exc}") from exc


def _write_config(path: str, data: Dict):
    config_dir = os.path.dirname(path)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def setup_logging(logging_config: LoggingConfig):
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)
    if logging_config.logfile:
        log_dir = os.path.dirname(os.path.abspath(logging_config.logfile))
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(logging_config.logfile, mode="a")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    level = getattr(logging, logging_config.level.upper(), logging.INFO)
    root.setLevel(level)


def read_config(args: Optional[argparse.Namespace] = None) -> Config:
    """Read a candidate without changing the running configuration or logging."""
    parsed = args or parse_arguments()
    config = load_config(parsed.config)
    if parsed.logfile:
        config.logging.logfile = parsed.logfile
    if parsed.log_level:
        config.logging.level = parsed.log_level
    return config


def set_cached_config(config: Config) -> None:
    global _cached_config
    _cached_config = config


def get_config(args: Optional[argparse.Namespace] = None) -> Config:
    if _cached_config is not None:
        return _cached_config
    config = read_config(args)
    setup_logging(config.logging)
    set_cached_config(config)
    return config


def reset_cached_config():
    global _cached_config
    _cached_config = None


def generate_and_store_api_key(config_path: Optional[str], key_id: str) -> str:
    if not key_id:
        raise ValueError("--genkey requires an API key id")
    path = config_path or DEFAULT_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Configuration file not found at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in configuration file {path}: {exc}") from exc

    api_keys = data.get("api_keys") or []
    target = next((item for item in api_keys if item.get("id") == key_id), None)
    if target is None:
        raise KeyError(f"API key id '{key_id}' not found in configuration")

    base_kdf = dict(target.get("kdf") or {})
    base_kdf.pop("salt", None)
    kdf = ensure_kdf_defaults(base_kdf)
    new_key = generate_random_api_key()
    kdf["hash"] = derive_argon2id_hash(new_key, kdf)
    target["kdf"] = kdf

    Config(**data)
    _write_config(path, data)
    logger.info("Updated API key entry '%s' in %s", key_id, path)
    return new_key
