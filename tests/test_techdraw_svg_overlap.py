"""Estimated, transform-aware text overlap over exported TechDraw SVG."""
import pytest

from mcpfreecad.freecad import techdraw_svg_overlap as overlap

SVG_HEADER = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2970 2100">'
SVG_FOOTER = '</svg>'


def _page(*text_groups):
    body = '\n'.join(text_groups)
    return (SVG_HEADER + '<g id="DrawingContent">' + body + '</g>' + SVG_FOOTER).encode('utf-8')


def _text_group(content, tx, ty, font_size=67, matrix=None, local_x=4, local_y=92.2188):
    transform = matrix or f'matrix(1,0,0,1,{tx},{ty})'
    return (f'<g transform="{transform}"><text font-size="{font_size}" x="{local_x}" '
        f'y="{local_y}" xml:space="preserve">{content}</text></g>')


def test_no_overlap_between_two_well_separated_labels():
    svg = _page(_text_group('60.00', 970.4, 1222.0), _text_group('30.00', 970.8, 500.0))
    assert overlap.find_overlaps(svg) == []


def test_overlap_between_two_labels_at_nearly_the_same_position():
    svg = _page(_text_group('60.00', 970.4, 1222.0), _text_group('30.00', 972.0, 1222.0))
    findings = overlap.find_overlaps(svg)
    hit = [f for f in findings if f['check'] == 'svg_text_overlap']
    assert len(hit) == 1 and hit[0]['severity'] == 'warning' and hit[0]['texts'] == ['30.00', '60.00']


def test_rotated_text_box_is_transformed_before_comparing():
    # matrix(0,-1,1,0,tx,ty) is FreeCAD's real export pattern for vertical
    # (rotated) dimension text; placing two rotated labels at nearly the same
    # transformed position must still be caught.
    svg = _page(
        _text_group('40.00', None, None, matrix='matrix(0,-1,1,0,552.0,1050.31)'),
        _text_group('10.00', None, None, matrix='matrix(0,-1,1,0,553.0,1051.0)'))
    findings = overlap.find_overlaps(svg)
    assert any(f['check'] == 'svg_text_overlap' for f in findings)


def test_rotated_text_far_apart_is_not_flagged():
    svg = _page(
        _text_group('40.00', None, None, matrix='matrix(0,-1,1,0,552.0,1050.31)'),
        _text_group('10.00', None, None, matrix='matrix(0,-1,1,0,900.0,1050.31)'))
    assert overlap.find_overlaps(svg) == []


def test_empty_text_is_ignored():
    svg = _page(_text_group('', 970.4, 1222.0), _text_group('   ', 970.4, 1222.0))
    assert overlap.find_overlaps(svg) == []


def test_numeric_decimal_point_labels_are_reported_for_iso_review():
    report=overlap.analyze_svg(_page(_text_group('⌀6.6',0,0),_text_group('6,6',0,100),
        _text_group('Part001',0,200),_text_group('2026-11-24',0,300)))
    assert report['decimal_point_labels']==['⌀6.6']


def test_missing_drawing_content_group_returns_no_findings():
    svg = (SVG_HEADER + '<g id="SomethingElse"></g>' + SVG_FOOTER).encode('utf-8')
    assert overlap.find_overlaps(svg) == []


def test_three_overlapping_labels_report_each_pair():
    svg = _page(_text_group('A', 100., 100.), _text_group('B', 101., 100.), _text_group('C', 102., 100.))
    findings = overlap.find_overlaps(svg)
    pairs = {tuple(f['texts']) for f in findings if f['check'] == 'svg_text_overlap'}
    assert pairs == {('A', 'B'), ('A', 'C'), ('B', 'C')}


@pytest.mark.parametrize('matrix_text,expected', [
    ('matrix(1,0,0,1,10,20)', (1.0, 0.0, 0.0, 1.0, 10.0, 20.0)),
    (None, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
])
def test_parse_matrix_falls_back_to_identity_on_malformed_input(matrix_text, expected):
    assert overlap._parse_matrix(matrix_text) == expected


def test_apply_matrix_handles_rotation():
    matrix = (0.0, -1.0, 1.0, 0.0, 0.0, 0.0)  # 90-degree rotation
    assert overlap._apply_matrix(matrix, 1.0, 0.0) == pytest.approx((0.0, -1.0))


def test_nested_transforms_and_inherited_font_are_composed():
    svg=_page('<g transform="translate(100 0)" font-size="20"><g transform="scale(2)"><text x="0" y="20">AAA</text></g></g>',
        '<text font-size="40" x="101" y="40">BBB</text>')
    result=overlap.analyze_svg(svg)
    assert result['labels_checked']==2 and result['findings']
    assert result['coverage_complete'] and not result['glyph_metrics_measured']


def test_text_anchor_and_non_drawingcontent_text_are_checked():
    svg=(SVG_HEADER+'<text font-size="20" text-anchor="end" x="100" y="100">AAAA</text>'+
        '<text font-size="20" x="60" y="100">BBBB</text>'+SVG_FOOTER).encode()
    assert overlap.analyze_svg(svg)['findings']


def test_unsupported_content_and_limits_are_explicit(monkeypatch):
    result=overlap.analyze_svg(_page('<g transform="unknown(1)"><text>A</text></g>'))
    assert not result['coverage_complete'] and 'unsupported_transform' in result['coverage_gaps']
    monkeypatch.setattr(overlap,'MAX_LABELS',1)
    result=overlap.analyze_svg(_page(_text_group('A',0,0),_text_group('B',0,0)))
    assert 'text_label_limit' in result['coverage_gaps']


def test_entity_expansion_and_nonfinite_transforms_are_refused():
    with pytest.raises(ValueError):overlap.analyze_svg(b'<!DOCTYPE svg [<!ENTITY x "abc">]><svg/>')
    with pytest.raises(ValueError):overlap._parse_matrix('matrix(1,0,0,1,1e999,0)')
    with pytest.raises(ValueError):overlap._parse_matrix('not a matrix')


def test_plain_qt_template_tspan_preserves_coverage():
    svg=_page('<text font-size="10" x="10" y="10"><tspan xml:space="preserve">Title</tspan></text>')
    report=overlap.analyze_svg(svg)
    assert report['coverage_complete'] and report['labels_checked']==1


def test_font_metrics_use_ink_bounds_and_advance_for_anchor():
    svg=_page('<g font-family="Test" font-weight="bold"><text font-size="10" text-anchor="end" x="100" y="20">Wide</text></g>',
        '<line x1="92" y1="0" x2="92" y2="30" stroke="black"/>')
    calls=[]
    def metrics(text,size,style):
        calls.append((text,size,style['font-family'],style['font-weight']))
        return 10.,2.,-7.,8.,1.
    result=overlap.analyze_svg(svg,font_metrics=metrics)
    assert calls==[('Wide',10.,'Test','bold')]
    assert result['glyph_metrics_measured'] and result['measured_labels']==1
    assert result['estimated_labels']==0
    assert result['line_geometry']['glyph_metrics_measured']


def test_font_metric_failure_reports_estimated_fallback():
    def metrics(*args):
        raise ValueError('Font unavailable')
    result=overlap.analyze_svg(_page('<text>abc</text>'),font_metrics=metrics)
    assert not result['glyph_metrics_measured'] and result['estimated_labels']==1
    assert 'font_metrics_fallback' in result['coverage_gaps']


def test_explicit_text_length_does_not_use_unadjusted_font_metrics():
    result=overlap.analyze_svg(_page('<text textLength="20">abc</text>'),font_metrics=lambda *args: (1,0,-1,1,0))
    assert result['estimated_labels']==1 and not result['glyph_metrics_measured']
