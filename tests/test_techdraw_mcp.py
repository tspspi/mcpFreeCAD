"""TechDraw previews cross MCP as image content, with no client file access."""
import asyncio
import base64
from copy import deepcopy
import hashlib
import struct
from types import SimpleNamespace
import zlib

from mcp import Client
import pytest

from mcpfreecad.app import server as app
from mcpfreecad.app.techdraw_tools import preview_result
from mcpfreecad.config.schema import Config
from mcpfreecad.freecad import bridge_server, techdraw


def preview(width=128, height=256):
    def chunk(kind, value):
        return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value))
    png = (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+
        chunk(b'IDAT',zlib.compress((b'\0'+b'\xff\xff\xff'*width)*height))+chunk(b'IEND',b''))
    return dict(document='Fixture',page='Sheet',mime_type='image/png',width=width,height=height,
        byte_length=len(png),sha256=hashlib.sha256(png).hexdigest(),
        data_base64=base64.b64encode(png).decode())


@pytest.fixture
def server(tmp_path):
    config=Config();config.stdio.library_root=str(tmp_path/'library')
    return app._build_fastmcp_server(config)


def test_serialized_mcp_contains_image_and_page_metadata(server,monkeypatch):
    instance,_=server;expected=preview();calls=[]
    def call(command,**params):
        calls.append((command,params));return deepcopy(expected)
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=call))
    async def run():
        async with Client(instance) as client:
            names={tool.name for tool in (await client.list_tools()).tools}
            assert techdraw.COMMANDS <= names
            result=await client.call_tool('render_techdraw_page',dict(page_name='Sheet',width=128,height=256))
            images=[item for item in result.content if item.type=='image']
            assert len(images)==1 and images[0].mime_type=='image/png'
            assert images[0].data==expected['data_base64']
            assert result.structured_content['sha256']==expected['sha256']
            assert result.structured_content['page']=='Sheet'
            assert 'data_base64' not in result.structured_content
    asyncio.run(run())
    assert calls[0][0]=='render_techdraw_page' and calls[0][1]['page_name']=='Sheet'


def test_snapshot_permission_refuses_before_bridge_call(server,monkeypatch):
    instance,state=server
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:pytest.fail('Bridge reached without snapshot permission'))
    key=state.stdio_key.model_copy(update={'allow_snapshots':False})
    original_key=state.stdio_key;state.stdio_key=key
    try:
        with pytest.raises(RuntimeError,match='snapshots'):
            instance._tool_manager.get_tool('render_techdraw_page').fn(page_name='Sheet')
    finally:state.stdio_key=original_key


@pytest.mark.parametrize('size',[(0,128),(128,5000),(4096,4096),(True,128),(128.,128)])
def test_preview_rejects_unbounded_or_invalid_dimensions(size):
    with pytest.raises(ValueError):techdraw.image_size(*size)


@pytest.mark.parametrize('change',[
    {'mime_type':'text/plain'}, {'sha256':'0'*64}, {'width':256}, {'byte_length':1},
    {'data_base64':'invalid'}, {'data_base64':' '}, {'data_base64':None}])
def test_preview_rejects_corrupt_bridge_bytes(change):
    response=preview();response.update(change)
    with pytest.raises(ValueError):preview_result(response,128,256)


def test_preview_rejects_image_header_dimensions_even_with_current_digest():
    response=preview(256,128);response.update(width=128,height=256)
    with pytest.raises(ValueError):preview_result(response,128,256)


def test_template_path_cannot_escape_installed_templates(tmp_path):
    root=tmp_path/'Mod/TechDraw/Templates';root.mkdir(parents=True)
    (root/'good.svg').write_text('<svg/>');(tmp_path/'outside.svg').write_text('<svg/>')
    (root/'link.svg').symlink_to(tmp_path/'outside.svg')
    App=SimpleNamespace(getResourceDir=lambda:str(tmp_path))
    assert techdraw._template_path(App,'good.svg')==root/'good.svg'
    for name in ('../../../outside.svg',str(tmp_path/'outside.svg'),'link.svg'):
        with pytest.raises(ValueError):techdraw._template_path(App,name)


def test_techdraw_commands_run_on_gui_dispatcher_without_3d_autofit():
    for command in techdraw.COMMANDS:
        assert bridge_server._command_requires_gui_dispatch(command,{},SimpleNamespace())
        assert command not in bridge_server._CAD_AUTOFIT_COMMANDS


def test_projection_wait_drains_workers_started_by_completion_callbacks(monkeypatch):
    clock=iter([0.,0.,0.,0.]);monkeypatch.setattr(techdraw.time,'monotonic',lambda:next(clock))
    finished=iter([True,False,True,True]);active=iter([1,0,0]);events=[]
    pool=SimpleNamespace(waitForDone=lambda ms:next(finished),activeThreadCount=lambda:next(active))
    qt=SimpleNamespace(QThreadPool=SimpleNamespace(globalInstance=lambda:pool),
        QCoreApplication=SimpleNamespace(processEvents=lambda flags:events.append(flags)),
        QEventLoop=SimpleNamespace(ExcludeUserInputEvents=123))
    techdraw._wait_for_projection(qt,1.)
    assert events==[123]*8


def test_projection_wait_refuses_busy_pool_at_deadline(monkeypatch):
    monkeypatch.setattr(techdraw.time,'monotonic',lambda:2.)
    qt=SimpleNamespace(QThreadPool=SimpleNamespace(globalInstance=lambda:object()))
    with pytest.raises(RuntimeError,match='still updating'):techdraw._wait_for_projection(qt,1.)


def test_existing_view_accepts_a_view_already_on_the_page():
    view=SimpleNamespace(Name='Top')
    page=SimpleNamespace(getAllViews=lambda:[view])
    doc=SimpleNamespace(getObject=lambda name:view)
    assert techdraw._existing_view(doc,page,'Top') is view


def test_existing_view_refuses_a_name_not_on_the_page():
    page=SimpleNamespace(getAllViews=lambda:[SimpleNamespace(Name='Top')])
    doc=SimpleNamespace(getObject=lambda name:SimpleNamespace(Name=name))
    with pytest.raises(ValueError):techdraw._existing_view(doc,page,'Other')


def test_base_part_view_accepts_a_projected_part_view():
    view=SimpleNamespace(Name='Top',TypeId='TechDraw::DrawViewPart')
    page=SimpleNamespace(getAllViews=lambda:[view])
    doc=SimpleNamespace(getObject=lambda name:view)
    assert techdraw._base_part_view(doc,page,'Top') is view


@pytest.mark.parametrize('type_id',['TechDraw::DrawViewSection','TechDraw::DrawViewDetail','TechDraw::DrawViewAnnotation'])
def test_base_part_view_refuses_non_plain_part_views(type_id):
    view=SimpleNamespace(Name='Top',TypeId=type_id)
    page=SimpleNamespace(getAllViews=lambda:[view])
    doc=SimpleNamespace(getObject=lambda name:view)
    with pytest.raises(ValueError):techdraw._base_part_view(doc,page,'Top')


def test_vector3_accepts_three_finite_components():
    assert techdraw._vector3([1,2.5,-3],'Test vector')==[1.,2.5,-3.]


@pytest.mark.parametrize('value',[None,[1.,2.],[1.,2.,float('nan')],[1.,2.,'3'],(1.,2.,3.,4.)])
def test_vector3_rejects_non_finite_or_mis_shaped_input(value):
    with pytest.raises(ValueError):techdraw._vector3(value,'Test vector')


@pytest.mark.parametrize('export_format',['dxf','',None,'SVG'])
def test_export_drawing_rejects_unsupported_formats(export_format):
    with pytest.raises(ValueError):techdraw.export_drawing(None,None,None,export_format)


def test_export_drawing_requires_gui_for_a_supported_format():
    with pytest.raises(RuntimeError,match='GUI'):techdraw.export_drawing(None,None,None,'svg')


def test_new_view_section_detail_and_export_commands_stay_in_the_techdraw_registry():
    assert {'update_techdraw_view','create_techdraw_section_view','create_techdraw_detail_view',
        'export_techdraw_drawing','check_techdraw_conformance'} <= techdraw.COMMANDS


def test_serialized_mcp_exposes_conformance_check(server,monkeypatch):
    instance,_=server;calls=[]
    def call(command,**params):
        calls.append((command,params))
        return dict(document='Fixture',page='Sheet',conformance_certified=False,standard_family='din-iso',
            findings=[],error_count=0,warning_count=0,info_count=0,blank_title_block_fields=[],
            vision_checklist=['Check line weights.'])
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=call))
    async def run():
        async with Client(instance) as client:
            names={tool.name for tool in (await client.list_tools()).tools}
            assert 'check_techdraw_conformance' in names
            result=(await client.call_tool('check_techdraw_conformance',
                dict(page_name='Sheet'))).structured_content
            assert result['conformance_certified'] is False and result['findings']==[]
            assert result['vision_checklist']==['Check line weights.']
    asyncio.run(run())
    assert calls[0][0]=='check_techdraw_conformance'
    assert calls[0][1]['page_name']=='Sheet' and calls[0][1]['document_name'] is None


def test_serialized_mcp_exposes_view_update_section_detail_and_export(server,monkeypatch):
    instance,_=server;calls=[]
    def call(command,**params):
        calls.append((command,params))
        if command=='export_techdraw_drawing':
            return dict(document='Fixture',page='Sheet',format='svg',mime_type='image/svg+xml',
                byte_length=6,sha256=hashlib.sha256(b'<svg/>').hexdigest(),data_base64='PHN2Zy8+')
        return dict(document='Fixture',page='Sheet',views=[])
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=call))
    async def run():
        async with Client(instance) as client:
            names={tool.name for tool in (await client.list_tools()).tools}
            assert {'update_techdraw_view','create_techdraw_section_view','create_techdraw_detail_view',
                'export_techdraw_drawing'} <= names
            await client.call_tool('update_techdraw_view',dict(page_name='Sheet',view_name='Top',scale=2.))
            await client.call_tool('create_techdraw_section_view',dict(page_name='Sheet',base_view_name='Top',
                section_normal=[1.,0.,0.],section_origin=[0.,0.,0.]))
            await client.call_tool('create_techdraw_detail_view',dict(page_name='Sheet',base_view_name='Top',
                anchor_point_mm=[10.,20.],radius_mm=5.))
            result=await client.call_tool('export_techdraw_drawing',dict(page_name='Sheet',format='svg'))
            assert result.structured_content['format']=='svg' and result.structured_content['sha256']==hashlib.sha256(b'<svg/>').hexdigest()
    asyncio.run(run())
    names=[entry[0] for entry in calls]
    assert names==['update_techdraw_view','create_techdraw_section_view','create_techdraw_detail_view',
        'export_techdraw_drawing']
    assert calls[0][1]['scale']==2. and calls[0][1]['x_mm'] is None and calls[0][1]['direction'] is None
    assert calls[1][1]['section_normal']==[1.,0.,0.] and calls[1][1]['section_direction']=='Right'
    assert calls[2][1]['anchor_point_mm']==[10.,20.] and calls[2][1]['radius_mm']==5.
    assert calls[3][1]['format']=='svg'


def test_conformance_render_check_obeys_snapshot_permission(server,monkeypatch):
    instance,state=server;calls=[]
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=lambda command,**params:calls.append((command,params)) or {}))
    original_key=state.stdio_key;state.stdio_key=state.stdio_key.model_copy(update={'allow_snapshots':False})
    try:
        tool=instance._tool_manager.get_tool('check_techdraw_conformance')
        with pytest.raises(RuntimeError,match='snapshots'):tool.fn(page_name='Sheet')
        assert not calls
        tool.fn(page_name='Sheet',check_rendered_text=False)
        assert calls[-1][1]['check_rendered_text'] is False
    finally:state.stdio_key=original_key


def test_export_checks_bridge_digest_before_returning_bytes(server,monkeypatch):
    instance,_=server
    response=dict(format='svg',mime_type='image/svg+xml',byte_length=6,sha256='0'*64,data_base64='PHN2Zy8+')
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=lambda *args,**params:response))
    with pytest.raises(ValueError,match='digest'):
        instance._tool_manager.get_tool('export_techdraw_drawing').fn(page_name='Sheet')


def test_title_update_preserves_existing_page_objects(server,monkeypatch):
    instance,_=server;calls=[]
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=lambda command,**params:calls.append((command,params)) or {'page':'Sheet'}))
    instance._tool_manager.get_tool('update_techdraw_page').fn(page_name='Sheet',editable_texts={'title':'Short title'})
    assert calls[0][0]=='update_techdraw_page' and calls[0][1]['editable_texts']=={'title':'Short title'}
    assert bridge_server._command_requires_gui_dispatch('update_techdraw_page',{},SimpleNamespace())


def test_view_frames_tool_routes_explicit_page_and_boolean(server,monkeypatch):
    instance,_=server;calls=[]
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=lambda command,**params:calls.append((command,params)) or {'show_frames':False}))
    result=instance._tool_manager.get_tool('set_techdraw_view_frames').fn(page_name='Sheet',enabled=False,document_name='Part').structured_content
    assert result['show_frames'] is False
    assert len(calls)==1 and calls[0][0]=='set_techdraw_view_frames'
    assert {k:v for k,v in calls[0][1].items() if k!='library_root'}==dict(page_name='Sheet',enabled=False,document_name='Part')
    assert bridge_server._command_requires_gui_dispatch('set_techdraw_view_frames',{},SimpleNamespace())


def test_detail_base_accepts_a_section_when_explicitly_enabled():
    view=SimpleNamespace(Name='Cut',TypeId='TechDraw::DrawViewSection')
    page=SimpleNamespace(getAllViews=lambda:[view]);doc=SimpleNamespace(getObject=lambda name:view)
    assert techdraw._base_part_view(doc,page,'Cut',allow_section=True) is view
    view.TypeId='TechDraw::DrawViewDetail'
    with pytest.raises(ValueError):techdraw._base_part_view(doc,page,'Cut',allow_section=True)
