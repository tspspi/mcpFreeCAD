"""API key utilities for Argon2id hashing and validation."""

from __future__ import annotations

import base64
import hmac
import os
from typing import Dict, Iterable, Optional

from argon2.low_level import Type, hash_secret_raw

from mcpfreecad.config.schema import APIKeyConfig


DEFAULT_KDF = {
    "algorithm": "argon2id",
    "salt": None,
    "time_cost": 3,
    "memory_cost": 65536,
    "parallelism": 1,
    "hash_len": 32,
}


def ensure_kdf_defaults(kdf: Dict) -> Dict:
    data = DEFAULT_KDF.copy()
    data.update(kdf or {})
    if data["algorithm"] != "argon2id":
        raise ValueError("Only argon2id is supported")
    if data.get("salt") is None:
        data["salt"] = base64.b64encode(os.urandom(16)).decode()
    return data


def derive_argon2id_hash(key: str, kdf: Dict) -> str:
    salt = base64.b64decode(kdf["salt"])
    raw = hash_secret_raw(
        secret=key.encode("utf-8"),
        salt=salt,
        time_cost=kdf["time_cost"],
        memory_cost=kdf["memory_cost"],
        parallelism=kdf["parallelism"],
        hash_len=kdf["hash_len"],
        type=Type.ID,
    )
    return base64.b64encode(raw).decode()


def generate_random_api_key(length: int = 48) -> str:
    return base64.urlsafe_b64encode(os.urandom(length)).decode().rstrip("=")


def _hash_with_kdf(key: str, kdf: Dict) -> Optional[str]:
    if not kdf or kdf.get("algorithm") not in (None, "argon2id"):
        return None
    required_fields = ("salt", "time_cost", "memory_cost", "parallelism", "hash_len")
    if not all(field in kdf for field in required_fields):
        return None
    try:
        raw = hash_secret_raw(
            secret=key.encode("utf-8"),
            salt=base64.b64decode(kdf["salt"]),
            time_cost=int(kdf["time_cost"]),
            memory_cost=int(kdf["memory_cost"]),
            parallelism=int(kdf["parallelism"]),
            hash_len=int(kdf["hash_len"]),
            type=Type.ID,
        )
    except Exception:
        return None
    return base64.b64encode(raw).decode("ascii")


def verify_token_against_key(token: str, key_config: APIKeyConfig) -> bool:
    if not token:
        return False
    stored_hash = (key_config.kdf or {}).get("hash")
    if not stored_hash:
        return False
    computed = _hash_with_kdf(token, key_config.kdf)
    if computed is None:
        return False
    return hmac.compare_digest(stored_hash, computed)


def match_api_key(token: str, api_keys: Iterable[APIKeyConfig]) -> Optional[APIKeyConfig]:
    if not token:
        return None
    for entry in api_keys:
        if verify_token_against_key(token, entry):
            return entry
    return None
