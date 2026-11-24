"""Pure structural conformance checks over already-fetched TechDraw page/dimension JSON."""
import pytest

from mcpfreecad.freecad import techdraw_conformance as conformance


def _page(views, scale=1.0, profile=None, editable_texts=None):
    return dict(scale=scale, views=views, drawing_profile=profile,
        template=dict(editable_texts=editable_texts or {}))


def _view(name, type_id='TechDraw::DrawViewPart', x=0., y=0., scale=1.0, **extra):
    return dict(name=name, type=type_id, x_mm=x, y_mm=y, scale=scale, **extra)


def _page_with_template(views, width_mm=297., height_mm=210., preset=None, **kwargs):
    page = _page(views, **kwargs)
    page['template']['width_mm'] = width_mm
    page['template']['height_mm'] = height_mm
    if preset is not None:
        if page.get('drawing_profile') is None:
            page['drawing_profile'] = {}
        page['drawing_profile']['preset'] = preset
    return page


def _edge(name, curve='Line', length_mm=None, radius_mm=None):
    entry = dict(name=name, curve=curve)
    if length_mm is not None:
        entry['length_mm'] = length_mm
    if radius_mm is not None:
        entry['radius_mm'] = radius_mm
    return entry


def test_clean_page_has_no_findings():
    page = _page([_view('Top', x=90., y=120.)], scale=1.0,
        profile=dict(standard_family='din-iso'),
        editable_texts=dict(title='Box', general_tolerances='ISO 2768-mK'))
    report = conformance.check_page(page, [])
    assert report['findings'] == []
    assert report['conformance_certified'] is False
    assert (report['error_count'], report['warning_count'], report['info_count']) == (0, 0, 0)


def test_iso_dimensions_use_consistent_lettering_height():
    page=_page([_view('Top')],profile=dict(standard_family='din-iso'))
    dims=[dict(name='D1',font_size_mm=3.5),dict(name='D2',font_size_mm=5.)]
    report=conformance.check_page(page,dims)
    assert any(f['check']=='iso_dimension_text_height_mismatch' for f in report['findings'])
    page['drawing_profile']['standard_family']='asme'
    assert not any(f['check']=='iso_dimension_text_height_mismatch' for f in conformance.check_page(page,dims)['findings'])


def test_section_needs_identification_in_addition_to_cutting_plane_symbol():
    section=_view('Section',type_id='TechDraw::DrawViewSection',section_symbol='A',x=200,y=100)
    page=_page([section],profile=dict(standard_family='din-iso'))
    assert any(f['check']=='section_identification_unverified' for f in conformance.check_page(page,[])['findings'])
    page['views'].append(_view('SectionCaption',type_id='TechDraw::DrawViewAnnotation',x=200,y=140,text=['SECTION A-A']))
    assert not any(f['check'].startswith('section_identification_') for f in conformance.check_page(page,[])['findings'])
    page['views'][-1]['y_mm']=80
    assert any(f['check']=='section_identification_below_view' for f in conformance.check_page(page,[])['findings'])


def test_duplicate_section_letters_are_ambiguous():
    page=_page([_view('S1',type_id='TechDraw::DrawViewSection',section_symbol='A',caption='A-A'),
        _view('S2',type_id='TechDraw::DrawViewSection',section_symbol='A',caption='A-A',x=100)],
        profile=dict(standard_family='din-iso'))
    assert any(f['check']=='duplicate_section_identification' for f in conformance.check_page(page,[])['findings'])


def test_general_tolerance_text_does_not_establish_feature_scope():
    page=_page([_view('Top')],editable_texts={'general_tolerances':'Approved general specification'})
    report=conformance.check_page(page,[dict(name='D1',upper_tolerance=0.,lower_tolerance=0.)])
    assert any(f['check']=='general_tolerance_scope_unverified' for f in report['findings'])


def test_views_sharing_a_position_are_flagged_as_an_error():
    page = _page([_view('Section1', x=240., y=100.), _view('Detail1', x=240., y=100.)])
    report = conformance.check_page(page, [])
    duplicate = next(f for f in report['findings'] if f['check'] == 'duplicate_view_position')
    assert duplicate['severity'] == 'warning' and duplicate['views'] == ['Detail1', 'Section1']
    assert report['error_count'] == 0 and report['warning_count'] >= 1


def test_views_at_distinct_positions_are_not_flagged():
    page = _page([_view('Section1', x=240., y=100.), _view('Detail1', x=240., y=-50.)])
    report = conformance.check_page(page, [])
    assert not any(f['check'] == 'duplicate_view_position' for f in report['findings'])


@pytest.mark.parametrize('scale', [1.0, 2.0, 0.5, 5.0, 0.1, 20.0])
def test_preferred_iso5455_scales_are_not_flagged(scale):
    report = conformance.check_page(_page([_view('Top', scale=scale)], scale=scale), [])
    assert not any(f['check'] == 'non_preferred_scale' for f in report['findings'])


@pytest.mark.parametrize('scale', [1.3, 3.0, 0.4, 7.0])
def test_non_preferred_scales_are_flagged_as_informational_only(scale):
    report = conformance.check_page(_page([_view('Top', scale=scale)], scale=scale), [])
    findings = [f for f in report['findings'] if f['check'] == 'non_preferred_scale']
    assert len(findings) == 2  # the page scale and the one view's own scale
    assert all(f['severity'] == 'info' for f in findings)
    assert report['info_count'] == 2 and report['error_count'] == 0


def test_asme_dimension_style_on_a_declared_din_iso_sheet_is_flagged():
    page = _page([_view('Dim1', type_id='TechDraw::DrawViewDimension')], profile=dict(standard_family='din-iso'))
    dimensions = [dict(name='Dim1', style='ASME Inlined', upper_tolerance=0., lower_tolerance=0., arbitrary=False)]
    report = conformance.check_page(page, dimensions)
    mismatch = [f for f in report['findings'] if f['check'] == 'dimension_style_family_mismatch']
    assert len(mismatch) == 1 and mismatch[0]['severity'] == 'warning'


def test_matching_dimension_style_family_is_not_flagged():
    page = _page([_view('Dim1', type_id='TechDraw::DrawViewDimension')], profile=dict(standard_family='din-iso'))
    dimensions = [dict(name='Dim1', style='ISO Oriented', upper_tolerance=0., lower_tolerance=0., arbitrary=False)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'dimension_style_family_mismatch' for f in report['findings'])


def test_undeclared_profile_family_skips_the_style_check():
    page = _page([_view('Dim1', type_id='TechDraw::DrawViewDimension')], profile=None)
    dimensions = [dict(name='Dim1', style='ASME Inlined', upper_tolerance=0., lower_tolerance=0., arbitrary=False)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'dimension_style_family_mismatch' for f in report['findings'])
    assert report['standard_family'] is None


def test_zero_tolerance_dimension_flagged_only_without_a_general_tolerance_note():
    page_blank = _page([_view('Dim1', type_id='TechDraw::DrawViewDimension')])
    dimensions = [dict(name='Dim1', style='ISO Oriented', upper_tolerance=0., lower_tolerance=0., arbitrary=False)]
    assert any(f['check'] == 'undocumented_tolerance' for f in conformance.check_page(page_blank, dimensions)['findings'])

    page_noted = _page([_view('Dim1', type_id='TechDraw::DrawViewDimension')],
        editable_texts=dict(general_tolerances='ISO 2768-mK'))
    assert not any(f['check'] == 'undocumented_tolerance'
        for f in conformance.check_page(page_noted, dimensions)['findings'])


def test_explicit_tolerance_is_never_flagged_as_undocumented():
    page = _page([_view('Dim1', type_id='TechDraw::DrawViewDimension')])
    dimensions = [dict(name='Dim1', style='ISO Oriented', upper_tolerance=0.1, lower_tolerance=-0.1, arbitrary=False)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'undocumented_tolerance' for f in report['findings'])


def test_arbitrary_dimension_is_never_flagged_as_undocumented():
    page = _page([_view('Dim1', type_id='TechDraw::DrawViewDimension')])
    dimensions = [dict(name='Dim1', style='ISO Oriented', upper_tolerance=0., lower_tolerance=0., arbitrary=True)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'undocumented_tolerance' for f in report['findings'])


def test_section_view_without_a_symbol_is_flagged():
    page = _page([_view('Section1', type_id='TechDraw::DrawViewSection', section_symbol='')])
    report = conformance.check_page(page, [])
    assert any(f['check'] == 'missing_section_symbol' for f in report['findings'])


def test_section_view_with_a_symbol_is_not_flagged():
    page = _page([_view('Section1', type_id='TechDraw::DrawViewSection', section_symbol='AA')])
    report = conformance.check_page(page, [])
    assert not any(f['check'] == 'missing_section_symbol' for f in report['findings'])


def test_blank_title_block_fields_are_reported_separately_from_findings():
    page = _page([], editable_texts=dict(title='Box', general_tolerances='', drawing_number=''))
    report = conformance.check_page(page, [])
    assert report['blank_title_block_fields'] == ['drawing_number', 'general_tolerances']
    assert report['findings'] == []


def test_view_within_margin_is_not_flagged():
    page = _page_with_template([_view('TopView', x=90., y=130., scale=1.0)])
    report = conformance.check_page(page, [], view_extents={'TopView': 30.0})
    assert not any(f['check'] == 'view_extends_past_sheet_edge' for f in report['findings'])


def test_view_reaching_past_the_right_edge_is_flagged():
    # Mirrors a real failure: a section view's own anchor was inside the page,
    # but its drawn content (radius * scale) reached past the right edge.
    page = _page_with_template([_view('SectionAA', x=250., y=130., scale=2.0)])
    report = conformance.check_page(page, [], view_extents={'SectionAA': 30.0})
    hit = [f for f in report['findings'] if f['check'] == 'view_extends_past_sheet_edge']
    assert len(hit) == 1 and hit[0]['severity'] == 'warning' and hit[0]['view'] == 'SectionAA'


def test_view_reaching_past_the_left_edge_is_flagged():
    page = _page_with_template([_view('TopView', x=20., y=130., scale=2.0)])
    report = conformance.check_page(page, [], view_extents={'TopView': 30.0})
    assert any(f['check'] == 'view_extends_past_sheet_edge' for f in report['findings'])


def test_view_missing_from_extents_mapping_is_not_flagged():
    page = _page_with_template([_view('Mystery', x=-9999., y=9999., scale=1.0)])
    report = conformance.check_page(page, [], view_extents={})
    assert not any(f['check'] == 'view_extends_past_sheet_edge' for f in report['findings'])


def test_undimensioned_feature_size_is_flagged():
    geometry = {'TopView': [_edge('Edge0', curve='Circle', radius_mm=30.0), _edge('Edge1', length_mm=14.0)]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry, referenced_edges=set())
    hit = [f for f in report['findings'] if f['check'] == 'unreferenced_feature_size']
    assert {f['size_mm'] for f in hit} == {30.0, 14.0}
    assert all(f['severity'] == 'warning' for f in hit)


def test_dimensioned_feature_size_is_not_flagged():
    geometry = {'TopView': [_edge('Edge0', curve='Circle', radius_mm=30.0)]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry,
        referenced_edges={('TopView', 'Edge0')})
    assert not any(f['check'] == 'unreferenced_feature_size' for f in report['findings'])


def test_dimensioning_one_of_two_equal_length_edges_covers_both():
    # Two rectangle sides of the same length are the same "size" to rebuild from;
    # dimensioning either one should clear the finding for both.
    geometry = {'TopView': [_edge('Edge1', length_mm=14.0), _edge('Edge3', length_mm=14.0)]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry,
        referenced_edges={('TopView', 'Edge3')})
    assert not any(f['check'] == 'unreferenced_feature_size' for f in report['findings'])


def test_same_size_on_a_different_view_does_not_count_as_referenced():
    geometry = {'TopView': [_edge('Edge0', length_mm=14.0)], 'SectionAA': [_edge('Edge0', length_mm=14.0)]}
    report = conformance.check_page(_page([_view('TopView'), _view('SectionAA')]), [], view_geometry=geometry,
        referenced_edges={('SectionAA', 'Edge0')})
    hit = [f for f in report['findings'] if f['check'] == 'unreferenced_feature_size']
    assert len(hit) == 1 and hit[0]['view'] == 'TopView'


def _dimension(name, view, x=0., y=0., **extra):
    return dict(name=name, references=[dict(view=view, names=['Edge0'])], **extra)


def test_dimension_placed_outside_the_view_silhouette_is_not_flagged():
    page = _page([_view('TopView', x=90., y=130., scale=1.0),
        _view('HoleLoc', type_id='TechDraw::DrawViewDimension', x=20., y=-40.)])
    dimensions = [_dimension('HoleLoc', 'TopView')]
    report = conformance.check_page(page, dimensions, view_extents={'TopView': 30.0})
    assert not any(f['check'] == 'dimension_inside_view_silhouette' for f in report['findings'])


def test_dimension_placed_inside_the_view_silhouette_is_flagged():
    page = _page([_view('TopView', x=90., y=130., scale=1.0),
        _view('BadDim', type_id='TechDraw::DrawViewDimension', x=5., y=5.)])
    dimensions = [_dimension('BadDim', 'TopView')]
    report = conformance.check_page(page, dimensions, view_extents={'TopView': 30.0})
    hit = [f for f in report['findings'] if f['check'] == 'dimension_inside_view_silhouette']
    assert len(hit) == 1 and hit[0]['severity'] == 'warning' and hit[0]['view'] == 'TopView'


def test_dimension_silhouette_check_scales_with_view_scale():
    # Same relative offset, but a 2x view scale means the drawn silhouette reaches twice as far.
    page = _page([_view('TopView', x=90., y=130., scale=2.0),
        _view('BadDim', type_id='TechDraw::DrawViewDimension', x=40., y=0.)])
    dimensions = [_dimension('BadDim', 'TopView')]
    report = conformance.check_page(page, dimensions, view_extents={'TopView': 30.0})
    assert any(f['check'] == 'dimension_inside_view_silhouette' for f in report['findings'])


def test_dimension_missing_extent_data_is_not_flagged():
    page = _page([_view('TopView', x=90., y=130., scale=1.0),
        _view('SomeDim', type_id='TechDraw::DrawViewDimension', x=1., y=1.)])
    dimensions = [_dimension('SomeDim', 'TopView')]
    report = conformance.check_page(page, dimensions, view_extents={})
    assert not any(f['check'] == 'dimension_inside_view_silhouette' for f in report['findings'])


def test_two_annotations_close_together_are_flagged():
    page = _page([_view('NoteA', type_id='TechDraw::DrawViewAnnotation', x=100., y=100.),
        _view('NoteB', type_id='TechDraw::DrawViewAnnotation', x=105., y=103.)])
    report = conformance.check_page(page, [])
    hit = [f for f in report['findings'] if f['check'] == 'elements_placed_close_together']
    assert len(hit) == 1 and hit[0]['severity'] == 'info' and hit[0]['elements'] == ['NoteA', 'NoteB']


def test_two_annotations_far_apart_are_not_flagged():
    page = _page([_view('NoteA', type_id='TechDraw::DrawViewAnnotation', x=30., y=30.),
        _view('NoteB', type_id='TechDraw::DrawViewAnnotation', x=200., y=150.)])
    report = conformance.check_page(page, [])
    assert not any(f['check'] == 'elements_placed_close_together' for f in report['findings'])


def test_dimension_and_annotation_proximity_resolves_parent_view_offset():
    # Dimension at relative (5,5) off a view anchored at (90,130) -> absolute (95,135);
    # an annotation at (96,136) is less than 2mm away in absolute terms.
    page = _page([_view('TopView', x=90., y=130., scale=1.0),
        _view('CloseDim', type_id='TechDraw::DrawViewDimension', x=5., y=5.),
        _view('CloseNote', type_id='TechDraw::DrawViewAnnotation', x=96., y=136.)])
    dimensions = [_dimension('CloseDim', 'TopView')]
    report = conformance.check_page(page, dimensions)
    hit = [f for f in report['findings'] if f['check'] == 'elements_placed_close_together']
    assert len(hit) == 1 and set(hit[0]['elements']) == {'CloseDim', 'CloseNote'}


def _circle_edge(name, center_mm, radius_mm=5.0):
    return dict(name=name, curve='Circle', center_mm=center_mm, radius_mm=radius_mm)


def _vertex(name, point_mm):
    return dict(name=name, kind='vertex', point_mm=point_mm)


def test_circle_on_the_view_origin_needs_no_position_dimension():
    geometry = {'TopView': [_circle_edge('Edge0', [0.0, 0.0, 0.0])]}
    vertices = {'TopView': [_vertex('Vertex0', [0.0, 0.0, 0.0])]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry,
        view_vertices=vertices, referenced_edges=set())
    assert not any(f['check'] == 'unreferenced_feature_position' for f in report['findings'])


def test_off_axis_circle_with_unreferenced_centre_is_flagged():
    geometry = {'TopView': [_circle_edge('Edge6', [18.0, 0.0, 0.0])]}
    vertices = {'TopView': [_vertex('Vertex11', [18.0, 0.0, 0.0])]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry,
        view_vertices=vertices, referenced_edges=set())
    hit = [f for f in report['findings'] if f['check'] == 'unreferenced_feature_position']
    assert len(hit) == 1 and hit[0]['severity'] == 'warning' and hit[0]['edge'] == 'Edge6'


def test_off_axis_circle_with_referenced_centre_is_not_flagged():
    geometry = {'TopView': [_circle_edge('Edge6', [18.0, 0.0, 0.0])]}
    vertices = {'TopView': [_vertex('Vertex11', [18.0, 0.0, 0.0])]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry,
        view_vertices=vertices, referenced_edges={('TopView', 'Vertex11')})
    assert not any(f['check'] == 'unreferenced_feature_position' for f in report['findings'])


def test_off_axis_circle_with_no_matching_vertex_is_not_flagged():
    # No selectable centre vertex found for this circle in the supplied vertex list --
    # cannot check, so stay silent rather than guess.
    geometry = {'TopView': [_circle_edge('Edge6', [18.0, 0.0, 0.0])]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry,
        view_vertices={}, referenced_edges=set())
    assert not any(f['check'] == 'unreferenced_feature_position' for f in report['findings'])


def test_two_off_axis_circles_dimensioned_to_each_other_are_both_satisfied():
    geometry = {'TopView': [_circle_edge('Edge5', [-10.0, 0.0, 0.0]), _circle_edge('Edge6', [18.0, 0.0, 0.0])]}
    vertices = {'TopView': [_vertex('VertexA', [-10.0, 0.0, 0.0]), _vertex('VertexB', [18.0, 0.0, 0.0])]}
    report = conformance.check_page(_page([_view('TopView')]), [], view_geometry=geometry, view_vertices=vertices,
        referenced_edges={('TopView', 'VertexA'), ('TopView', 'VertexB')})
    assert not any(f['check'] == 'unreferenced_feature_position' for f in report['findings'])


@pytest.mark.parametrize('width_mm,height_mm', [
    (297., 210.),   # A4 landscape
    (420., 297.),   # A3 landscape
    (594., 420.),   # A2 landscape
    (841., 594.),   # A1 landscape
    (1189., 841.),  # A0 landscape
    (210., 297.),   # A4 portrait
])
def test_view_reaching_into_the_title_block_is_flagged_on_every_sheet_size(width_mm, height_mm):
    # The title block is a fixed 180x48 mm rectangle 10 mm in from the right/bottom
    # edges on every one of this project's own ISO5457 preset sizes; its near corner
    # is at (width-190, 58) in TechDraw's bottom-up page-coordinate convention.
    near_corner_x, near_corner_y = width_mm - 190.0, 58.0
    page = _page_with_template([_view('View1', x=near_corner_x + 5.0, y=near_corner_y - 5.0, scale=1.0)],
        width_mm=width_mm, height_mm=height_mm, preset='iso-a4-landscape')
    report = conformance.check_page(page, [], view_extents={'View1': 10.0})
    hit = [f for f in report['findings'] if f['check'] == 'view_overlaps_title_block']
    assert len(hit) == 1 and hit[0]['severity'] == 'warning' and hit[0]['view'] == 'View1'


def test_view_clear_of_the_title_block_is_not_flagged():
    page = _page_with_template([_view('View1', x=90., y=130., scale=1.0)], preset='iso-a4-landscape')
    report = conformance.check_page(page, [], view_extents={'View1': 30.0})
    assert not any(f['check'] == 'view_overlaps_title_block' for f in report['findings'])


def test_title_block_check_is_skipped_without_a_known_preset():
    # A custom/explicit template's title-block position is unknown -- do not guess.
    page = _page_with_template([_view('View1', x=280., y=20., scale=1.0)], preset=None)
    report = conformance.check_page(page, [], view_extents={'View1': 30.0})
    assert not any(f['check'] == 'view_overlaps_title_block' for f in report['findings'])


def _linear_dimension(name, view, x, y):
    return dict(name=name, type='DistanceY', references=[dict(view=view, names=['Edge0'])])


def test_two_stacked_dimensions_at_the_same_offset_are_flagged():
    # Reproduces a real failure mode: two length dimensions off the same view, placed
    # the same distance out from it, differing only in position along the line itself --
    # FreeCAD draws them as one continuous line with two labels, not two distinguishable
    # dimensions, however far apart their own positions are along that shared line.
    page = _page([_view('FrontView', x=150., y=150., scale=2.0),
        _view('LowerLength', type_id='TechDraw::DrawViewDimension', x=40., y=0.),
        _view('UpperLength', type_id='TechDraw::DrawViewDimension', x=40., y=20.)])
    dimensions = [_linear_dimension('LowerLength', 'FrontView', 40., 0.),
        _linear_dimension('UpperLength', 'FrontView', 40., 20.)]
    report = conformance.check_page(page, dimensions)
    hit = [f for f in report['findings'] if f['check'] == 'dimensions_share_offset_line']
    assert len(hit) == 1 and hit[0]['severity'] == 'warning'
    assert hit[0]['dimensions'] == ['LowerLength', 'UpperLength'] and hit[0]['view'] == 'FrontView'


def test_staggered_dimension_offsets_are_not_flagged():
    page = _page([_view('FrontView', x=150., y=150., scale=2.0),
        _view('LowerLength', type_id='TechDraw::DrawViewDimension', x=40., y=0.),
        _view('UpperLength', type_id='TechDraw::DrawViewDimension', x=55., y=20.)])
    dimensions = [_linear_dimension('LowerLength', 'FrontView', 40., 0.),
        _linear_dimension('UpperLength', 'FrontView', 55., 20.)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'dimensions_share_offset_line' for f in report['findings'])


def test_same_offset_on_both_axes_is_not_flagged_as_collinear():
    # Already a duplicate_view_position case, not this one -- do not double-flag identical points.
    page = _page([_view('FrontView', x=150., y=150., scale=2.0),
        _view('DimA', type_id='TechDraw::DrawViewDimension', x=40., y=0.),
        _view('DimB', type_id='TechDraw::DrawViewDimension', x=40., y=0.)])
    dimensions = [_linear_dimension('DimA', 'FrontView', 40., 0.), _linear_dimension('DimB', 'FrontView', 40., 0.)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'dimensions_share_offset_line' for f in report['findings'])


def test_shared_offset_within_the_proximity_threshold_is_not_double_flagged_as_collinear():
    # Close enough together that elements_placed_close_together already covers it;
    # dimensions_share_offset_line is reserved for the far-apart-but-collinear case.
    page = _page([_view('FrontView', x=150., y=150., scale=2.0),
        _view('DimA', type_id='TechDraw::DrawViewDimension', x=40., y=0.),
        _view('DimB', type_id='TechDraw::DrawViewDimension', x=40., y=5.)])
    dimensions = [_linear_dimension('DimA', 'FrontView', 40., 0.), _linear_dimension('DimB', 'FrontView', 40., 5.)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'dimensions_share_offset_line' for f in report['findings'])
    assert any(f['check'] == 'elements_placed_close_together' for f in report['findings'])


def test_dimensions_on_different_parent_views_are_not_compared():
    page = _page([_view('FrontView', x=150., y=150., scale=2.0), _view('TopView', x=50., y=50., scale=1.0),
        _view('DimA', type_id='TechDraw::DrawViewDimension', x=40., y=0.),
        _view('DimB', type_id='TechDraw::DrawViewDimension', x=40., y=20.)])
    dimensions = [_linear_dimension('DimA', 'FrontView', 40., 0.), _linear_dimension('DimB', 'TopView', 40., 20.)]
    report = conformance.check_page(page, dimensions)
    assert not any(f['check'] == 'dimensions_share_offset_line' for f in report['findings'])


def test_vision_checklist_is_always_present_and_nonempty():
    report = conformance.check_page(_page([]), [])
    assert report['vision_checklist'] and all(isinstance(item, str) for item in report['vision_checklist'])
    assert report['conformance_certified'] is False


def test_dimension_local_offsets_do_not_collide_across_parent_views():
    page=_page([_view('A',x=70,y=120),_view('B',x=200,y=120),
        _view('DA',type_id='TechDraw::DrawViewDimension',x=0,y=-30),
        _view('DB',type_id='TechDraw::DrawViewDimension',x=0,y=-30)])
    dimensions=[_linear_dimension('DA','A',0,-30),_linear_dimension('DB','B',0,-30)]
    report=conformance.check_page(page,dimensions)
    assert not any(f['check']=='duplicate_view_position' for f in report['findings'])


def test_circle_and_line_equal_numbers_are_distinct_feature_sizes():
    geometry={'Top':[_edge('Edge0',length_mm=5),_edge('Edge1',curve='Circle',radius_mm=5)]}
    report=conformance.check_page(_page([_view('Top')]),[],view_geometry=geometry,referenced_edges={('Top','Edge0')})
    assert any(f['check']=='unreferenced_feature_size' and f['edges']==['Edge1'] for f in report['findings'])


def test_duplicate_centre_vertices_accept_any_matching_reference():
    geometry={'Top':[_circle_edge('Edge0',[.1,0,0])]}
    vertices={'Top':[_vertex('Vertex0',[.1,0,0]),_vertex('Vertex1',[.1,0,0])]}
    report=conformance.check_page(_page([_view('Top')]),[],view_geometry=geometry,view_vertices=vertices,
        referenced_edges={('Top','Vertex1')})
    assert not any(f['check']=='unreferenced_feature_position' for f in report['findings'])
    report=conformance.check_page(_page([_view('Top')]),[],view_geometry=geometry,view_vertices=vertices)
    assert any(f['check']=='unreferenced_feature_position' for f in report['findings'])


def test_angle_reference_does_not_specify_line_length():
    dimensions=[dict(name='Angle',type='Angle',references=[dict(view='Top',names=['Edge0','Edge1'])])]
    geometry={'Top':[_edge('Edge0',length_mm=5),_edge('Edge1',length_mm=6)]}
    report=conformance.check_page(_page([_view('Top')]),dimensions,view_geometry=geometry,
        referenced_edges={('Top','Edge0'),('Top','Edge1')})
    assert len([f for f in report['findings'] if f['check']=='unreferenced_feature_size'])==2


def test_diameter_anchors_are_not_treated_as_collinear_length_dimensions():
    page=_page([_view('Top'),_view('D1',type_id='TechDraw::DrawViewDimension',x=30,y=20),
        _view('D2',type_id='TechDraw::DrawViewDimension',x=30,y=-20)])
    dimensions=[dict(name=n,type='Diameter',references=[dict(view='Top',names=['Edge0'])]) for n in ('D1','D2')]
    report=conformance.check_page(page,dimensions)
    assert not any(f['check']=='dimensions_share_offset_line' for f in report['findings'])


def test_findings_are_bounded_and_omissions_are_explicit():
    page=_page([_view('V'+str(i)) for i in range(256)])
    page['views'] += [_view('Extra')]
    with pytest.raises(ValueError,match='256'):conformance.check_page(page,[])
    geometry={'Top':[_edge('Edge'+str(i),length_mm=i+1) for i in range(600)]}
    report=conformance.check_page(_page([_view('Top')]),[],view_geometry=geometry)
    assert len(report['findings'])<=512 and report['findings_omitted']>0


def test_visible_editing_frames_are_a_finish_workflow_warning():
    page=_page([]);page['show_frames']=True
    assert any(f['check']=='editing_frames_visible' for f in conformance.check_page(page,[])['findings'])
    page['show_frames']=False
    assert not any(f['check']=='editing_frames_visible' for f in conformance.check_page(page,[])['findings'])


def test_split_circle_arcs_do_not_repeat_the_same_position_warning():
    geometry={'Top':[_circle_edge('Edge0',[10.,0,0]),_circle_edge('Edge1',[10.,0,0])]}
    vertices={'Top':[dict(name='Vertex0',point_mm=[10.,0,0])]}
    report=conformance.check_page(_page([_view('Top')]),[],view_geometry=geometry,view_vertices=vertices)
    assert len([f for f in report['findings'] if f['check']=='unreferenced_feature_position'])==1
