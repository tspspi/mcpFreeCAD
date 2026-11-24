"""MCP SDK transport migration preserves per-request API-key permissions."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from mcpfreecad.app import server as app
from mcpfreecad.config.schema import APIKeyConfig, Config, RemoteServerConfig
from test_techdraw_mcp import preview


def _payload(response):
    response.raise_for_status()
    if response.headers.get('content-type','').startswith('application/json'):
        return response.json()
    return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))


def test_remote_tool_calls_keep_request_key_permissions(tmp_path,monkeypatch):
    config=Config(remote_server=RemoteServerConfig(host='127.0.0.1',port=18080),api_keys=[
        APIKeyConfig(id='restricted',kdf={},library_root=str(tmp_path/'restricted'),allow_snapshots=False),
        APIKeyConfig(id='allowed',kdf={},library_root=str(tmp_path/'allowed'),allow_snapshots=True)])
    monkeypatch.setattr(app,'match_api_key',lambda token,keys:next((k for k in keys if k.id==token),None))
    calls=[]
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *args:SimpleNamespace(
        call=lambda command,**params:calls.append((command,params)) or preview()))
    server,state=app._build_fastmcp_server(config)
    with TestClient(app._build_remote_fastapi_app(server,state)) as client:
        assert client.post('/mcp/',json={}).status_code==401
        for key in ['restricted','allowed']:
            headers={'Authorization':'Bearer '+key,'Accept':'application/json, text/event-stream'}
            response=client.post('/mcp/',headers=headers,json=dict(jsonrpc='2.0',id=1,method='initialize',params={
                'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}))
            assert _payload(response)['result']['serverInfo']['name']=='mcpfreecad'
            headers['Mcp-Session-Id']=response.headers['Mcp-Session-Id']
            headers['Mcp-Protocol-Version']='2025-03-26'
            response=client.post('/mcp/',headers=headers,json=dict(jsonrpc='2.0',method='notifications/initialized'))
            assert response.status_code==202
            packet=_payload(client.post('/mcp/',headers=headers,json=dict(jsonrpc='2.0',id=2,method='tools/call',params={
                'name':'render_techdraw_page','arguments':{'page_name':'Sheet','width':128,'height':256}})))
            if key=='restricted':
                assert packet['result']['isError'] and not calls
            else:
                assert not packet['result'].get('isError',False)
                assert any(c['type']=='image' for c in packet['result']['content'])
                assert calls[-1][1]['library_root']==str(tmp_path/'allowed')
