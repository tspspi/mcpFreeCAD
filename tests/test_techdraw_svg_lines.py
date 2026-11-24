"""Rendered straight strokes are checked independently of dimension style/type."""
import xml.etree.ElementTree as ET
import pytest
from mcpfreecad.freecad import techdraw_svg_lines as lines
from mcpfreecad.freecad.techdraw_svg_overlap import analyze_svg


def test_relative_and_repeated_path_commands_are_resolved():
    node=ET.fromstring('<path d="m 1,2 3,0 h 2 v 4 l -1,-1"/>')
    strokes,gap=lines.segments(node,dict(stroke='black',fill='none'))
    assert gap is None and strokes==[((1.,2.),(4.,2.)),((4.,2.),(6.,2.)),((6.,2.),(6.,6.)),((6.,6.),(5.,5.))]


@pytest.mark.parametrize('a,b,expected',[((-1,-1),(2,2),True),((-1,2),(2,2),False),((0,0),(0,0),True),((-1,0),(2,0),True)])
def test_clipping_handles_inclined_horizontal_and_zero_length_segments(a,b,expected):
    assert lines.intersects((0,0,1,1),a,b) is expected


def test_actual_nested_svg_stroke_crosses_a_label_regardless_of_dimension_family():
    svg=b'<svg xmlns="http://www.w3.org/2000/svg"><g transform="translate(10,20)"><text x="0" y="0" font-size="4">R5</text><g transform="rotate(45)"><path stroke="black" fill="none" d="M-10,0 L10,0"/></g></g></svg>'
    report=analyze_svg(svg)
    assert any(f['check']=='svg_text_line_overlap' for f in report['findings'])
    assert report['line_geometry']['segments_checked']==1


def test_curved_paths_report_line_coverage_gaps_without_breaking_text_coverage():
    report=analyze_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><path stroke="black" d="M0,0 C1,2 3,4 5,6"/></svg>')
    assert report['coverage_complete']
    assert not report['line_geometry']['coverage_complete']
    assert report['line_geometry']['coverage_gaps']==['curved_or_unsupported_path']


def test_filled_arrowheads_are_excluded_from_extension_line_checks():
    node=ET.fromstring('<path d="M0,0 L1,2 L-1,2 Z"/>')
    assert lines.segments(node,dict(stroke='black',fill='black'))==([],None)


def test_rotation_does_not_expand_a_text_rectangle_into_a_false_stroke_collision():
    svg=b'<svg xmlns="http://www.w3.org/2000/svg"><g transform="rotate(45)"><text font-size="10" x="0" y="0">LONG</text><path stroke="black" fill="none" d="M-1,4 L30,4"/></g></svg>'
    report=analyze_svg(svg)
    assert not any(f['check']=='svg_text_line_overlap' for f in report['findings'])
