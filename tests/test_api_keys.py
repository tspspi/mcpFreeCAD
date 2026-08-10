from mcpfreecad.config.api_keys import derive_argon2id_hash, ensure_kdf_defaults, match_api_key
from mcpfreecad.config.schema import APIKeyConfig


def make_api_key_config(tmp_path, *, key: str = "secret"):
    kdf = ensure_kdf_defaults({})
    kdf["hash"] = derive_argon2id_hash(key, kdf)
    return APIKeyConfig(id="alpha", kdf=kdf, library_root=str(tmp_path / "library"))


def test_match_api_key(tmp_path):
    config = make_api_key_config(tmp_path, key="abc123")
    other = make_api_key_config(tmp_path, key="second")

    assert match_api_key("abc123", [config, other]) == config
    assert match_api_key("missing", [config, other]) is None
