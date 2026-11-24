"""Dimension values must agree with geometry, independently of label cosmetics."""
import math
import pytest
from mcpfreecad.freecad.techdraw_measurement_checks import check_measurements


def check(kind,value,rows,spec='%.1f',**extra):
    index={'View':rows}
    dim=dict(name='Dim',type=kind,value=value,format_spec=spec,references=[dict(view='View',names=[r['name'] for r in rows])],**extra)
    findings=[]
    report=check_measurements([dim],index,index,findings.append)
    return findings,report


@pytest.mark.parametrize('kind,value,spec',[('Radius',5.,'R%.1f'),('Diameter',10.,'⌀%.1f')])
def test_radial_values_and_symbols_are_checked(kind,value,spec):
    edge=dict(name='Edge0',curve='Circle',radius_mm=5.)
    assert not check(kind,value,[edge],spec)[0]
    assert any(f['check']=='dimension_geometry_value_mismatch' for f in check(kind,value+1,[edge],spec)[0])
    assert any(f['check']=='dimension_semantic_symbol_missing' for f in check(kind,value,[edge])[0])


@pytest.mark.parametrize('kind,value',[('Distance',5.),('DistanceX',3.),('DistanceY',4.)])
def test_inclined_linear_values_use_the_requested_component(kind,value):
    rows=[dict(name='Vertex0',point_mm=[0,0,0]),dict(name='Vertex1',point_mm=[3,4,0])]
    findings,report=check(kind,value,rows)
    assert not findings and report['coverage_complete']


def test_vertex_angle_uses_middle_reference_and_allows_reflex_readback():
    rows=[dict(name='Vertex0',point_mm=[1,0,0]),dict(name='Vertex1',point_mm=[0,0,0]),dict(name='Vertex2',point_mm=[0,1,0])]
    assert not check('Angle3Pt',90.,rows,'%.1f')[0]
    assert not check('Angle3Pt',270.,rows,'%.1f',inverted=True)[0]
    assert any(f['severity']=='error' for f in check('Angle3Pt',45.,rows,'%.1f')[0])


def test_two_edge_angle_accepts_supplementary_orientation():
    rows=[dict(name='Edge0',curve='Line',vertices_mm=[[0,0,0],[1,0,0]]),dict(name='Edge1',curve='Line',vertices_mm=[[0,0,0],[1,1,0]])]
    assert not check('Angle',45.,rows,'%.1f')[0]
    assert not check('Angle',135.,rows,'%.1f')[0]


def test_closest_points_are_explicitly_distinguished_from_independent_vertex_checks():
    native=dict(available=True,points_mm=[[0,0,0],[0,7,0]])
    findings,report=check('Distance',7.,[],measurement_geometry=native)
    assert not findings and report['calculation_sources']['Dim']=='native_closest_points'


def test_arbitrary_values_are_not_accepted_as_geometric_measurements():
    findings,report=check('Distance',9.,[],arbitrary=True)
    assert any(f['check']=='arbitrary_dimension_value' and f['severity']=='error' for f in findings)
    assert not report['coverage_complete']


def test_missing_geometry_and_true_3d_measurements_report_skipped_coverage():
    assert not check('Diameter',10.,[])[1]['coverage_complete']
    findings,report=check('Distance',10.,[],measure_type='True')
    assert not findings and report['dimensions_skipped'][0]['reason']=='true_3d_measurement_requires_3d_geometry'


def test_point_to_edge_is_checked_in_python_without_native_points():
    rows=[dict(name='Vertex0',point_mm=[5,7,0]),dict(name='Edge0',curve='Line',vertices_mm=[[0,0,0],[10,0,0]])]
    findings,report=check('Distance',7.,rows)
    assert not findings and report['calculation_sources']['Dim']=='inspected_references'


def test_parallel_edge_separation_is_checked_in_python():
    rows=[dict(name='Edge0',curve='Line',vertices_mm=[[0,0,0],[10,0,0]]),dict(name='Edge1',curve='Line',vertices_mm=[[2,7,0],[8,7,0]])]
    assert not check('DistanceY',7.,rows)[0]
    assert any(f['severity']=='error' for f in check('Distance',8.,rows)[0])


def test_literal_angular_unit_is_flagged_because_native_formatter_adds_one():
    rows=[dict(name='Vertex0',point_mm=[1,0,0]),dict(name='Vertex1',point_mm=[0,0,0]),dict(name='Vertex2',point_mm=[0,1,0])]
    assert any(f['check']=='duplicate_angular_unit' for f in check('Angle3Pt',90.,rows,'%.1f°')[0])
