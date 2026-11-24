"""Failed reload candidates leave the running configuration in place."""
from types import SimpleNamespace
import pytest
from mcpfreecad.app import server as app
from mcpfreecad.config.schema import Config


def test_key_preparation_failure_preserves_state(tmp_path,monkeypatch):
    old=Config();state=SimpleNamespace(config=old,stdio_key=object())
    original_key=state.stdio_key
    def fail(config):raise OSError('Library unavailable')
    monkeypatch.setattr(app,'_build_stdio_key_config',fail)
    with pytest.raises(OSError):app._apply_new_config_to_state(state,Config())
    assert state.config is old and state.stdio_key is original_key


def test_invalid_reload_does_not_replace_cache_or_config(tmp_path,monkeypatch):
    config=Config();config.stdio.library_root=str(tmp_path/'library')
    _,state=app._build_fastmcp_server(config)
    handlers=[];changed=[]
    monkeypatch.setattr(app.signal,'signal',lambda signum,fn:handlers.append(fn))
    monkeypatch.setattr(app,'read_config',lambda args:Config())
    monkeypatch.setattr(app,'set_cached_config',lambda candidate:changed.append('cache'))
    monkeypatch.setattr(app,'setup_logging',lambda candidate:changed.append('logging'))
    app._install_remote_reload_handler(SimpleNamespace(),state)
    handlers[0](0,None)
    assert state.config is config and not changed
