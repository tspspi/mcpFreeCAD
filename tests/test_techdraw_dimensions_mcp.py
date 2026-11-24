"""Measured drawing tools expose inspected references and preserve MCP arguments."""
import asyncio
from types import SimpleNamespace
import pytest
from mcp import Client

from mcpfreecad.app import server as app
from mcpfreecad.freecad import bridge_server,techdraw_dimensions as dims
from test_techdraw_mcp import server


def test_serialized_mcp_exposes_geometry_dimensions_and_annotations(server,monkeypatch):
    instance,_=server;calls=[]
    def call(command,**params):
        calls.append((command,params))
        if command=='get_techdraw_view_geometry':
            return dict(geometry=[dict(name='Edge2',reference_token='a'*64,length_mm=40.)],has_more=False)
        if command=='create_techdraw_dimension':return dict(dimension=dict(value=40.,arbitrary=False))
        return dict(annotation='WorkshopNote')
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=call))
    async def run():
        async with Client(instance) as client:
            names={tool.name for tool in (await client.list_tools()).tools}
            assert dims.COMMANDS<=names
            result=(await client.call_tool('get_techdraw_view_geometry',dict(page_name='Sheet',view_name='Top'))).structured_content
            ref={key:result['geometry'][0][key] for key in ('name','reference_token')}
            created=(await client.call_tool('create_techdraw_dimension',dict(page_name='Sheet',view_name='Top',references=[ref],dimension_type='DistanceX',upper_tolerance=.1,lower_tolerance=-.05))).structured_content
            assert created['dimension']['value']==40. and not created['dimension']['arbitrary']
            note=(await client.call_tool('create_techdraw_annotation',dict(page_name='Sheet',text='Dimensions in mm.'))).structured_content
            assert note['annotation']=='WorkshopNote'
    asyncio.run(run())
    assert calls[1][1]['references']==[dict(name='Edge2',reference_token='a'*64)]
    assert calls[1][1]['upper_tolerance']==.1 and calls[1][1]['lower_tolerance']==-.05


def test_all_measured_drawing_operations_use_gui_dispatch_without_3d_autofit():
    for command in dims.COMMANDS:
        assert bridge_server._command_requires_gui_dispatch(command,{},SimpleNamespace())
        assert command not in bridge_server._CAD_AUTOFIT_COMMANDS


@pytest.mark.parametrize('name',['Edge-1','Edge01','Edge10000000','Face1','Edge1;import os',''])
def test_reference_names_refuse_noncanonical_or_executable_inputs(name):
    with pytest.raises(ValueError):dims.reference(None,name)


def test_reference_token_changes_with_projection_and_geometry():
    source=SimpleNamespace(Name='Block')
    view=SimpleNamespace(Name='Top',Source=[source],Direction=(0.,0.,1.),XDirection=(1.,0.,0.),Rotation=0.,Scale=2.,X=80.,Y=120.)
    doc=SimpleNamespace(Name='Drawing');page=SimpleNamespace(Name='Sheet')
    geometry=dict(name='Edge2',kind='edge',curve='Line',length_mm=40.,vertices_mm=[[-20.,0.,0.],[20.,0.,0.]])
    before=dims.token(doc,page,view,geometry)
    view.Scale=3.
    assert dims.token(doc,page,view,geometry)!=before
    view.Scale=2.;geometry['length_mm']=60.
    assert dims.token(doc,page,view,geometry)!=before


def test_stale_reference_is_refused_before_any_dimension_creation(monkeypatch):
    geometry=dict(name='Edge2',kind='edge',curve='Line',length_mm=40.)
    monkeypatch.setattr(dims,'reference',lambda *args:geometry)
    monkeypatch.setattr(dims,'token',lambda *args:'new-token')
    with pytest.raises(ValueError,match='changed'):
        dims._references(None,None,None,[dict(name='Edge2',reference_token='old-token')],'DistanceX')


def test_radius_refuses_straight_projected_edge(monkeypatch):
    monkeypatch.setattr(dims,'reference',lambda *args:dict(name='Edge2',kind='edge',curve='Line'))
    monkeypatch.setattr(dims,'token',lambda *args:'same-token')
    with pytest.raises(ValueError,match='does not match'):
        dims._references(None,None,None,[dict(name='Edge2',reference_token='same-token')],'Radius')


def test_page_membership_uses_document_names_across_native_python_wrappers():
    view=SimpleNamespace(Name='Top',isDerivedFrom=lambda kind:True)
    doc=SimpleNamespace(getObject=lambda name:view)
    page=SimpleNamespace(getAllViews=lambda:[SimpleNamespace(Name='Top')])
    assert dims._view(doc,page,'Top') is view


def test_dimension_lookup_accepts_an_existing_dimension_on_the_page():
    dim=SimpleNamespace(Name='Dim1',TypeId='TechDraw::DrawViewDimension')
    doc=SimpleNamespace(getObject=lambda name:dim)
    page=SimpleNamespace(getAllViews=lambda:[SimpleNamespace(Name='Dim1')])
    assert dims._dimension(doc,page,'Dim1') is dim


@pytest.mark.parametrize('type_id',['TechDraw::DrawViewPart','TechDraw::DrawViewAnnotation'])
def test_dimension_lookup_refuses_non_dimension_objects(type_id):
    dim=SimpleNamespace(Name='Dim1',TypeId=type_id)
    doc=SimpleNamespace(getObject=lambda name:dim)
    page=SimpleNamespace(getAllViews=lambda:[SimpleNamespace(Name='Dim1')])
    with pytest.raises(ValueError):dims._dimension(doc,page,'Dim1')


def test_update_techdraw_dimension_registered_and_gui_dispatched():
    assert 'update_techdraw_dimension' in dims.COMMANDS
    assert bridge_server._command_requires_gui_dispatch('update_techdraw_dimension',{},SimpleNamespace())
    assert 'update_techdraw_dimension' not in bridge_server._CAD_AUTOFIT_COMMANDS


def test_serialized_mcp_exposes_dimension_update(server,monkeypatch):
    instance,_=server;calls=[]
    def call(command,**params):
        calls.append((command,params))
        return dict(dimension=dict(value=40.,precision=3))
    monkeypatch.setattr(app,'FreeCADBridgeClient',lambda *a:SimpleNamespace(call=call))
    async def run():
        async with Client(instance) as client:
            result=(await client.call_tool('update_techdraw_dimension',
                dict(page_name='Sheet',dimension_name='Dim1',precision=3))).structured_content
            assert result['dimension']['precision']==3
    asyncio.run(run())
    assert calls[0][0]=='update_techdraw_dimension'
    assert calls[0][1]['page_name']=='Sheet' and calls[0][1]['dimension_name']=='Dim1'
    assert calls[0][1]['precision']==3
    assert calls[0][1]['x_mm'] is None and calls[0][1]['style'] is None and calls[0][1]['upper_tolerance'] is None


@pytest.mark.parametrize('kinds',[('edge','edge'),('edge','vertex'),('vertex','edge')])
def test_lengths_accept_inspected_closest_point_reference_pairs(monkeypatch,kinds):
    rows={str(i):dict(name=str(i),kind=k,curve='Line') for i,k in enumerate(kinds)}
    monkeypatch.setattr(dims,'reference',lambda view,name:rows[name])
    monkeypatch.setattr(dims,'token',lambda *args:'token')
    refs=[dict(name=name,reference_token='token') for name in rows]
    assert dims._references(None,None,None,refs,'Distance')==['0','1']


def test_dimension_native_points_are_normalized_by_parent_scale_and_y_direction():
    parent=SimpleNamespace(Name='View',Scale=2.)
    point=lambda x,y:SimpleNamespace(x=x,y=y,z=0.)
    dimension=SimpleNamespace(Name='Dim',Type='Distance',MeasureType='Projected',ViewObject=SimpleNamespace(StandardAndStyle='ISO Oriented'),FormatSpec='%.1f',Arbitrary=False,OverTolerance=0.,UnderTolerance=0.,State=[],X=0.,Y=20.,References2D=[(parent,['Edge0'])],getRawValue=lambda:5.,getLinearPoints=lambda:[point(0,0),point(6,-8)],getArrowPositions=lambda:[point(0,10),point(3,14)])
    result=dims.dimension_details(dimension)
    assert result['measurement_geometry']['points_mm']==[[0.,0.,0.],[3.,4.,0.]]
    assert result['measurement_geometry']['arrow_points_mm']==[[0.,10.,0.],[3.,14.,0.]]


def test_perpendicular_single_edge_cannot_silently_create_a_zero_length(monkeypatch):
    monkeypatch.setattr(dims,'reference',lambda *args:dict(name='Edge0',kind='edge',curve='Line',vertices_mm=[[0,0,0],[0,20,0]]))
    monkeypatch.setattr(dims,'token',lambda *args:'token')
    with pytest.raises(ValueError,match='perpendicular'):
        dims._references(None,None,None,[dict(name='Edge0',reference_token='token')],'DistanceX')


def test_creation_checks_raw_measurement_against_the_inspected_frame(monkeypatch):
    view=SimpleNamespace(Name='Cut')
    monkeypatch.setattr(dims,'reference',lambda *args:dict(name='Edge0',kind='edge',curve='Line',vertices_mm=[[0,0,0],[15,0,0]]))
    details=dict(name='Depth',type='DistanceX',value=0.,references=[dict(view='Cut',names=['Edge0'])])
    with pytest.raises(ValueError,match='disagrees'):
        dims._verify_dimension_geometry(view,['Edge0'],details)


@pytest.mark.parametrize('value',[-1.,0.,101.,float('inf'),True])
def test_dimension_text_height_must_be_positive_and_bounded(value):
    with pytest.raises(ValueError):dims._font_size(value)
