"""Pitch circles are derived from inspected centres, never guessed graphics."""
import math
from types import SimpleNamespace
import pytest
from mcpfreecad.freecad import techdraw_hole_patterns as patterns


def holes(angles=range(0,360,45)):
    return [dict(name='Edge'+str(i),curve='Circle',radius_mm=2.,
        center_mm=[12.+30.*math.cos(math.radians(a)),-8.+30.*math.sin(math.radians(a)),0.])
        for i,a in enumerate(angles)]


def test_offset_pitch_circle_and_orientation_are_measured():
    result=patterns.analyze(holes(range(15,360,45)))
    assert result['center_mm']==pytest.approx([12.,-8.,0.])
    assert result['pitch_diameter_mm']==pytest.approx(60.)
    assert result['hole_count']==8 and result['hole_diameter_mm']==4.
    assert result['equally_spaced'] and result['spacing_deg']==45.
    assert result['angles_deg'][0]==pytest.approx(15.)
    assert result['associativity']=='validated_snapshot'


def test_unequal_spacing_is_not_reported_as_equal():
    result=patterns.analyze(holes([0,60,180,270]))
    assert not result['equally_spaced'] and result['spacing_deg'] is None


@pytest.mark.parametrize('change', ['duplicate','noncircular','different_size','off_circle','collinear','too_many','nonfinite'])
def test_invalid_pattern_is_refused(change):
    rows=holes()
    if change=='duplicate':rows.append(rows[0])
    elif change=='noncircular':rows[0]['curve']='Ellipse'
    elif change=='different_size':rows[0]['radius_mm']=3.
    elif change=='off_circle':rows[-1]['center_mm'][0]+=1.
    elif change=='collinear':
        for i,row in enumerate(rows):row['center_mm']=[float(i),0.,0.]
    elif change=='too_many':rows=holes(range(65))
    elif change=='nonfinite':rows[0]['center_mm'][0]=math.nan
    with pytest.raises(ValueError):patterns.analyze(rows)


def test_stale_pattern_is_reported_as_error(monkeypatch):
    rows=holes();pattern=patterns.analyze(rows)
    record=dict(name='Pattern',references=[r['name'] for r in rows],cosmetic_tag='tag',**pattern)
    view=SimpleNamespace(Name='Top',MCPHolePatterns=__import__('json').dumps([record]))
    from mcpfreecad.freecad import techdraw_dimensions
    monkeypatch.setattr(techdraw_dimensions,'reference',lambda v,name:rows[int(name[4:])])
    rows[-1]['center_mm'][0]+=1.
    findings=[]
    assert not patterns.check(view,[],findings.append)
    assert findings[0]['check']=='hole_pattern_stale_or_unresolved' and findings[0]['severity']=='error'


def test_bad_metadata_is_bounded():
    with pytest.raises(ValueError):patterns.records(SimpleNamespace(MCPHolePatterns='x'*131073))
    with pytest.raises(ValueError):patterns.records(SimpleNamespace(MCPHolePatterns='{}'))


@pytest.mark.parametrize('defect',[None,'missing','solid','moved','retagged'])
def test_verified_linework_and_missing_changed_circle(monkeypatch,defect):
    rows=holes();pattern=patterns.analyze(rows)
    record=dict(name='Pattern',references=[r['name'] for r in rows],cosmetic_tag='tag',**pattern)
    cosmetic=SimpleNamespace(Tag='tag',Format=dict(style=1 if defect=='solid' else 4,visible=True))
    def by_tag(tag):
        if defect in ('missing','retagged'):raise ValueError('Missing edge')
        return cosmetic
    def by_selection(name):
        if defect=='missing':raise ValueError('Missing edge')
        return cosmetic
    view=SimpleNamespace(Name='Top',MCPHolePatterns=__import__('json').dumps([record]),
        getCosmeticEdge=by_tag,getCosmeticEdgeBySelection=by_selection)
    from mcpfreecad.freecad import techdraw_dimensions
    monkeypatch.setattr(techdraw_dimensions,'reference',lambda v,name:rows[int(name[4:])])
    circle=dict(name='Edge100',curve='Circle',center_mm=[13.,-8.,0.] if defect=='moved' else pattern['center_mm'],radius_mm=30.)
    findings=[];checked=patterns.check(view,[circle],findings.append)
    if defect not in (None,'retagged'):
        assert not checked and findings[0]['severity']=='error'
    else:
        assert checked[0]['edge']=='Edge100' and not findings
        if defect=='retagged':assert checked[0]['cosmetic_reference_resolution']=='unique_geometry_after_restore'
