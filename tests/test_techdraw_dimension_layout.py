"""Regressions for label/helper crossings seen in the live drawing exercise."""
from mcpfreecad.freecad.techdraw_dimension_layout import check_layout


def run(x=0.,y=-25.,scale=1.,view_x=100.,view_y=100.,kind='DistanceX',style='ISO Oriented'):
    page=dict(views=[dict(name='Top',x_mm=view_x,y_mm=view_y,scale=scale)],
        drawing_profile=dict(preset='iso-a4-landscape'),template=dict(width_mm=297.))
    dim=dict(name='Width',type=kind,value=20.,font_size_mm=3.5,format_spec='%.1f',
        x_mm=x,y_mm=y,style=style,references=[dict(view='Top',names=['Vertex0','Vertex1'])])
    vertices={'Top':[dict(name='Vertex0',point_mm=[-10,0,0]),dict(name='Vertex1',point_mm=[10,0,0])]}
    findings=[]
    report=check_layout(page,[dim],{},vertices,findings.append)
    return findings,report


def test_endpoint_label_crossing_is_detected_and_centered_label_clears_it():
    assert any(f['check']=='dimension_text_extension_line' for f in run(x=10.)[0])
    assert not run(x=0.)[0]


def test_scaled_geometry_uses_sheet_offsets_without_rescaling_dimension_position():
    assert any(f['check']=='dimension_text_extension_line' for f in run(x=20.,scale=2.)[0])
    assert not run(x=0.,scale=2.)[0]


def test_dimension_line_can_reach_title_block_even_when_projected_view_does_not():
    findings,_=run(x=0.,y=-60.,view_x=180.,view_y=100.)
    assert any(f['check']=='dimension_line_title_block' for f in findings)


def test_custom_template_does_not_get_assumed_title_block_check():
    page=dict(views=[dict(name='Top',x_mm=180.,y_mm=100.,scale=1.)])
    findings=[]
    report=check_layout(page,[],{},{},findings.append)
    assert not findings and report['title_block_bounds']=='unavailable'


def test_unsupported_dimension_style_is_explicitly_skipped():
    findings,report=run(x=10.,style='ASME Inlined')
    assert not findings and report['dimensions_skipped']==['Width']


def test_other_dimensions_extension_line_can_cross_an_unrelated_label():
    page=dict(views=[dict(name='Top',x_mm=0.,y_mm=100.,scale=1.)])
    refs=[dict(view='Top',names=['Vertex0','Vertex1'])]
    dims=[dict(name='Short',type='DistanceX',value=20.,x_mm=0.,y_mm=-25.,references=refs),
          dict(name='Long',type='DistanceX',value=20.,x_mm=30.,y_mm=-50.,references=refs)]
    vertices={'Top':[dict(name='Vertex0',point_mm=[0,0,0]),dict(name='Vertex1',point_mm=[20,0,0])]}
    findings=[]
    check_layout(page,dims,{},vertices,findings.append)
    assert any(f.get('dimension')=='Short' and f.get('extension_owner')=='Long' for f in findings)


def test_single_digit_label_is_not_assumed_as_wide_as_font_height():
    page=dict(views=[dict(name='Top',x_mm=0.,y_mm=100.,scale=1.)])
    dim=dict(name='Depth',type='DistanceY',value=5.,font_size_mm=5.,format_spec='%.0f',x_mm=-30.,y_mm=5.,references=[dict(view='Top',names=['Vertex0','Vertex1'])])
    vertices={'Top':[dict(name='Vertex0',point_mm=[0,2.5,0]),dict(name='Vertex1',point_mm=[0,7.5,0])]}
    findings=[]
    check_layout(page,[dim],{},vertices,findings.append)
    assert not findings
