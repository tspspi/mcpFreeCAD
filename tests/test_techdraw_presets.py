"""Sheet/view presets remain explicit, metric and independent of model scale."""
import asyncio
import math
from types import SimpleNamespace
import pytest
from mcp import Client

from mcpfreecad.app import server as app
from mcpfreecad.freecad import techdraw_presets as presets,bridge_server
from test_techdraw_mcp import server


def test_metric_default_and_sheet_dimensions_use_available_installed_templates(tmp_path):
    root=tmp_path/'Mod/TechDraw/Templates';root.mkdir(parents=True)
    for filename,width,height in presets.SHEETS.values():(root/filename).write_text('<svg/>')
    App=SimpleNamespace(getResourceDir=lambda:str(tmp_path))
    catalog=presets.catalog(App)
    assert catalog['default_page_preset']=='iso-a4-portrait'
    assert all(row['available'] and row['linear_unit']=='mm' for row in catalog['page_presets'])
    assert {tuple(sorted((row['width_mm'],row['height_mm']))) for row in catalog['page_presets']}=={
        (210.,297.),(297.,420.),(420.,594.),(594.,841.),(841.,1189.)}
    path,preset=presets.sheet_choice(App,None,None)
    assert preset=='iso-a4-portrait' and path.name=='A4_Portrait_ISO5457_advanced.svg'
    with pytest.raises(ValueError):presets.sheet_choice(App,path.name,'iso-a3-landscape')
    assert presets.sheet_choice(App,path.name,None)==(path,None)


@pytest.mark.parametrize('name',list(presets.VIEWS))
def test_named_view_axes_are_unit_perpendicular_and_keep_an_explicit_projection(name):
    normal,right,selected=presets.view_choice(name,None,None)
    assert selected==name
    assert math.isclose(math.hypot(*normal),1.) and math.isclose(math.hypot(*right),1.)
    assert abs(sum(a*b for a,b in zip(normal,right)))<1e-12
    if name=='isometric':assert all(math.isclose(abs(v),1/math.sqrt(3)) for v in normal)


def test_custom_horizontal_axis_is_preserved_with_default_top_direction():
    normal,right,name=presets.view_choice(None,None,[0.,1.,0.])
    assert normal==[0.,0.,1.] and right==[0.,1.,0.] and name is None


@pytest.mark.parametrize('args',[
    ('front',[0.,0.,1.],None),('missing',None,None),(None,[0.,0.,0.],None),
    (None,[0.,0.,1.],[0.,0.,1.]),(None,[float('inf'),0.,1.],None)])
def test_conflicting_or_invalid_projection_choices_are_refused(args):
    with pytest.raises(ValueError):presets.view_choice(*args)


def test_serialized_preset_options_reach_the_bridge_without_hidden_axis_overrides(server,monkeypatch):
    instance,_=server;calls=[]
    def call(command,**params):calls.append((command,params));return {'command':command}
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=call))
    async def run():
        async with Client(instance) as client:
            await client.call_tool('list_techdraw_presets',{})
            await client.call_tool('create_techdraw_page',{})
            await client.call_tool('create_techdraw_view',dict(page_name='Sheet',source_names=['Block'],view_preset='isometric'))
    asyncio.run(run())
    assert calls[0][0]=='list_techdraw_presets'
    assert calls[1][1]['template'] is None and calls[1][1]['preset'] is None
    assert calls[2][1]['view_preset']=='isometric' and calls[2][1]['direction'] is None
    assert calls[2][1]['scale'] is None
    assert bridge_server._command_requires_gui_dispatch('list_techdraw_presets',{},SimpleNamespace())
