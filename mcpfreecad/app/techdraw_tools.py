"""MCP TechDraw tools, including direct multimodal page previews."""
import base64
import hashlib
import struct
from functools import wraps
from typing import Optional

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ImageContent, TextContent
import json

from mcpfreecad.freecad.techdraw import MAX_PNG_BYTES, MAX_SVG_BYTES, MAX_PDF_BYTES, image_size


def preview_result(response, width, height):
    """Check bridge image bytes before exposing them as MCP image content."""
    image_size(width, height)
    if not isinstance(response, dict) or response.get('mime_type') != 'image/png':
        raise ValueError('Invalid drawing preview response')
    encoded = response.get('data_base64')
    if type(encoded) is not str or len(encoded) > 4 * ((MAX_PNG_BYTES + 2) // 3):
        raise ValueError('Drawing preview payload exceeds its limit')
    data = base64.b64decode(encoded, validate=True)
    if (len(data) < 33 or len(data) > MAX_PNG_BYTES or data[:8] != b'\x89PNG\r\n\x1a\n' or
            data[12:16] != b'IHDR' or struct.unpack('>II', data[16:24]) != (width, height) or
            response.get('width') != width or response.get('height') != height or
            response.get('byte_length') != len(data) or response.get('sha256') != hashlib.sha256(data).hexdigest()):
        raise ValueError('Drawing preview dimensions, size or digest differ from the bridge response')
    metadata = {key: value for key, value in response.items() if key != 'data_base64'}
    return CallToolResult(content=[ImageContent(type='image',data=encoded,mime_type='image/png'),
        TextContent(type='text',text=json.dumps(metadata))],structured_content=metadata)


def register_techdraw_tools(server, checked_call):
    def tool(**options):
        def decorate(function):
            @wraps(function)
            def invoke(*args, **kwargs):
                value = function(*args, **kwargs)
                if isinstance(value, dict):
                    return CallToolResult(content=[TextContent(type='text', text=json.dumps(value))],
                        structured_content=value)
                return value
            return server.tool(**options)(invoke)
        return decorate
    @tool(description='Update existing TechDraw title-block fields without recreating the sheet or its views/dimensions. Inspect get_techdraw_page for available editable keys. Only provided fields change; unknown keys are refused. Do not invent manufacturing specifications or approval data.')
    def update_techdraw_page(page_name: str, editable_texts: dict[str,str], document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx,'update_techdraw_page',page_name=page_name,editable_texts=editable_texts,document_name=document_name)

    @tool(description='Show or hide the editing frames of views on a TechDraw page. Enable when needed for manual selection/editing; disable when editing is finished, before final review/save/delivery. This controls editing frames, not the printed template border. Read back show_frames to confirm.')
    def set_techdraw_view_frames(page_name: str, enabled: bool, document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx,'set_techdraw_view_frames',page_name=page_name,enabled=enabled,document_name=document_name)

    @tool(description='List installed metric DIN/ISO sheet presets from A4 to A0 and named orthographic/isometric view bases. Default is A4 portrait; template/style selection alone does not certify drawing conformance.')
    def list_techdraw_presets(ctx: Context = None):
        return checked_call(ctx, 'list_techdraw_presets')

    @tool(description='List installed TechDraw SVG template names for drawing page creation.')
    def list_techdraw_templates(ctx: Context = None):
        return checked_call(ctx, 'list_techdraw_templates')

    @tool(description='List TechDraw drawing pages with template fields and view placements.')
    def list_techdraw_pages(document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'list_techdraw_pages', document_name=document_name)

    @tool(description='Inspect a TechDraw page, its editable template fields and projected view sources.')
    def get_techdraw_page(page_name: str, document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'get_techdraw_page', page_name=page_name, document_name=document_name)

    @tool(description='Create a TechDraw page. Defaults to metric DIN/ISO A4 portrait; choose a listed preset up to A0 or an explicit installed SVG template. Presets clear shipped example specifications and declare mm units; template/style selection does not certify the complete drawing.')
    def create_techdraw_page(name: str = 'DrawingPage', template: Optional[str] = None,
            preset: Optional[str] = None, scale: float = 1., editable_texts: Optional[dict[str, str]] = None,
            document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'create_techdraw_page', name=name, template=template, preset=preset, scale=scale,
            editable_texts=editable_texts, document_name=document_name)

    @tool(description='Add a source-linked part view using a named orthographic/isometric preset or explicit projection axes. Position uses sheet mm; omitted scale follows the page. Isometric projected lengths are foreshortened and are not true model dimensions.')
    def create_techdraw_view(page_name: str, source_names: list[str], name: str = 'DrawingView',
            direction: Optional[list[float]] = None, view_preset: Optional[str] = None, x_direction: Optional[list[float]] = None, x_mm: float = 100., y_mm: float = 120., scale: Optional[float] = None,
            document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'create_techdraw_view', page_name=page_name, source_names=source_names,
            name=name, direction=direction, view_preset=view_preset, x_direction=x_direction,
            x_mm=x_mm, y_mm=y_mm, scale=scale, document_name=document_name)

    @tool(description='Render a complete TechDraw sheet as an inline MCP PNG image for visual inspection. No client filesystem access is needed. Requires snapshot permission and FreeCAD GUI rendering.')
    def render_techdraw_page(page_name: str, width: int = 1600, height: int = 1200,
            document_name: Optional[str] = None, ctx: Context = None) -> CallToolResult:
        image_size(width, height)
        response = checked_call(ctx, 'render_techdraw_page', require_snapshots=True,
            page_name=page_name, width=width, height=height, document_name=document_name)
        return preview_result(response, width, height)

    @tool(description='Inspect selectable projected edges or vertices on a TechDraw part view. Coordinates and lengths are unscaled projected millimetres. Use returned names and reference tokens to create measured dimensions; inspect again after geometry or view placement changes.')
    def get_techdraw_view_geometry(page_name: str, view_name: str, kind: str = 'edges',
            offset: int = 0, limit: int = 64, document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'get_techdraw_view_geometry', page_name=page_name, view_name=view_name,
            kind=kind, offset=offset, limit=limit, document_name=document_name)

    @tool(description='Create a measured projected TechDraw dimension from inspected geometry references. Lengths accept one straight edge, two vertices, two edges (closest-point separation), or one edge and one vertex (point-to-edge separation). Radii/diameters use circular edges; angles use two straight edges or three vertices. Position is in sheet millimetres relative to the parent-view centre, independent of its scale. Linear values/tolerances use mm; angles use degrees. Styles use FreeCAD ISO/ASME conventions; this does not certify the complete drawing against a standard.')
    def create_techdraw_dimension(page_name: str, view_name: str, references: list[dict[str, str]],
            dimension_type: str = 'Distance', name: str = 'DrawingDimension',
            x_mm: float = 0., y_mm: float = 25., style: str = 'ISO Oriented', precision: int = 2,
            upper_tolerance: float = 0., lower_tolerance: float = 0.,
            document_name: Optional[str] = None, font_size_mm: Optional[float] = None, ctx: Context = None):
        return checked_call(ctx, 'create_techdraw_dimension', page_name=page_name, view_name=view_name,
            references=references, dimension_type=dimension_type, name=name, x_mm=x_mm, y_mm=y_mm,
            style=style, precision=precision, upper_tolerance=upper_tolerance, lower_tolerance=lower_tolerance,
            font_size_mm=font_size_mm,document_name=document_name)

    @tool(description='Add a multiline text annotation to a TechDraw sheet at page coordinates in millimetres.')
    def create_techdraw_annotation(page_name: str, text: str, name: str = 'DrawingNote',
            x_mm: float = 20., y_mm: float = 30., font_size_mm: float = 3.5,
            document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'create_techdraw_annotation', page_name=page_name, text=text, name=name,
            x_mm=x_mm, y_mm=y_mm, font_size_mm=font_size_mm, document_name=document_name)

    @tool(description='Reposition, rescale or redirect an existing TechDraw view in place. Position/scale apply to any view type; a new direction/view_preset is restricted to plain projected part views (not section/detail views). Provide at least one field to change; omitted fields keep their current value.')
    def update_techdraw_view(page_name: str, view_name: str, x_mm: Optional[float] = None, y_mm: Optional[float] = None,
            scale: Optional[float] = None, direction: Optional[list[float]] = None, view_preset: Optional[str] = None,
            x_direction: Optional[list[float]] = None, document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'update_techdraw_view', page_name=page_name, view_name=view_name, x_mm=x_mm,
            y_mm=y_mm, scale=scale, direction=direction, view_preset=view_preset, x_direction=x_direction,
            document_name=document_name)

    @tool(description='Inspect a measured TechDraw dimension after preparing its current projection. Returns raw value, unit, symbols/format, tolerances, references and available native measurement geometry. Does not accept arbitrary display text as a measurement.')
    def get_techdraw_dimension(page_name: str, dimension_name: str, document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx,'get_techdraw_dimension',page_name=page_name,dimension_name=dimension_name,document_name=document_name)

    @tool(description='Edit an existing measured TechDraw dimension in place: position, numeric precision, font size in mm, ISO/ASME style or signed tolerances. Provide at least one field to change; omitted fields keep their current value. Does not change which projected geometry the dimension measures.')
    def update_techdraw_dimension(page_name: str, dimension_name: str, x_mm: Optional[float] = None,
            y_mm: Optional[float] = None, style: Optional[str] = None, precision: Optional[int] = None,
            upper_tolerance: Optional[float] = None, lower_tolerance: Optional[float] = None,
            document_name: Optional[str] = None, font_size_mm: Optional[float] = None, ctx: Context = None):
        return checked_call(ctx, 'update_techdraw_dimension', page_name=page_name, dimension_name=dimension_name,
            x_mm=x_mm, y_mm=y_mm, style=style, precision=precision, upper_tolerance=upper_tolerance,
            lower_tolerance=lower_tolerance,font_size_mm=font_size_mm, document_name=document_name)

    @tool(description='Create drawing-only dash-dot pitch-circle linework from 3..64 inspected circular hole edges in one plain projected view. Fits and verifies the pitch circle, identical hole sizes and optionally equal spacing. Returns measured pitch diameter, count and angular positions. This is a validated snapshot; mandatory conformance checks flag changed references/linework. Inspect geometry again to dimension the resulting circle. Hole size/depth, pattern location and angular orientation still need dimensions; no manufacturing completeness or standards certification is implied.')
    def create_techdraw_hole_pattern(page_name: str, view_name: str, references: list[dict[str,str]],
            name: str = 'HolePattern', require_equal_spacing: bool = True,
            document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx,'create_techdraw_hole_pattern',page_name=page_name,view_name=view_name,
            references=references,name=name,require_equal_spacing=require_equal_spacing,document_name=document_name)

    @tool(description='Add a cutting-plane section view derived from an existing projected part view, using an explicit model-space section normal and origin. section_direction selects which side of the cut is drawn (Right/Left/Up/Down, default Right). Callers supply the 3D cutting geometry directly; this does not infer a cut line from 2D sheet coordinates or certify the section against a drawing standard.')
    def create_techdraw_section_view(page_name: str, base_view_name: str, section_normal: list[float],
            section_origin: list[float], section_direction: str = 'Right', symbol: Optional[str] = None,
            name: str = 'DrawingSection', x_mm: Optional[float] = None, y_mm: Optional[float] = None,
            scale: Optional[float] = None, document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'create_techdraw_section_view', page_name=page_name, base_view_name=base_view_name,
            section_normal=section_normal, section_origin=section_origin, section_direction=section_direction,
            symbol=symbol, name=name, x_mm=x_mm, y_mm=y_mm, scale=scale, document_name=document_name)

    @tool(description="Add a magnified detail view circling one anchor point on an existing projected part or section view. anchor_point_mm and radius_mm use that view's own unscaled projected millimetres, the same coordinate system returned by get_techdraw_view_geometry. scale is the detail view's own magnification, independent of the base view's scale.")
    def create_techdraw_detail_view(page_name: str, base_view_name: str, anchor_point_mm: list[float],
            radius_mm: float, name: str = 'DrawingDetail', scale: float = 2.0, x_mm: Optional[float] = None,
            y_mm: Optional[float] = None, document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'create_techdraw_detail_view', page_name=page_name, base_view_name=base_view_name,
            anchor_point_mm=anchor_point_mm, radius_mm=radius_mm, name=name, scale=scale, x_mm=x_mm, y_mm=y_mm,
            document_name=document_name)

    @tool(description="Export a complete TechDraw sheet as SVG or PDF bytes, delivered inline with no client filesystem access needed. Requires snapshot permission and FreeCAD GUI rendering; does not certify the drawing against a standard.")
    def export_techdraw_drawing(page_name: str, format: str = 'svg', document_name: Optional[str] = None,
            ctx: Context = None):
        if format not in ('svg','pdf'):
            raise ValueError('Choose an export format: svg or pdf')
        response = checked_call(ctx, 'export_techdraw_drawing', require_snapshots=True, page_name=page_name,
            format=format, document_name=document_name)
        maximum = MAX_SVG_BYTES if format == 'svg' else MAX_PDF_BYTES
        mime = 'image/svg+xml' if format == 'svg' else 'application/pdf'
        if not isinstance(response,dict) or response.get('format') != format or response.get('mime_type') != mime:
            raise ValueError('Invalid drawing export response')
        encoded = response.get('data_base64')
        if type(encoded) is not str or len(encoded) > 4*((maximum+2)//3):
            raise ValueError('Drawing export payload exceeds its limit')
        data = base64.b64decode(encoded,validate=True)
        if (not 0 < len(data) <= maximum or response.get('byte_length') != len(data) or
                response.get('sha256') != hashlib.sha256(data).hexdigest()):
            raise ValueError('Drawing export size or digest differs from bridge response')
        if format == 'pdf' and not data.startswith(b'%PDF-'):
            raise ValueError('Invalid drawing PDF header')
        if format == 'svg':
            import xml.etree.ElementTree as ET
            if b'<!ENTITY' in data.upper():
                raise ValueError('Drawing SVG entity declarations are unsupported')
            if ET.fromstring(data).tag not in ('svg','{http://www.w3.org/2000/svg}svg'):
                raise ValueError('Invalid drawing SVG root')
        return response

    @tool(description="Review a TechDraw page with bounded structural and optional SVG text-box heuristics. Returns findings, geometry/rendered-text coverage gaps and a visual-review checklist. Estimated bounds and character-count widths can be wrong; a clean result is neither dimensioning completeness nor DIN/ISO/ASME certification. Geometry inspection prepares current projections in the GUI. check_rendered_text defaults to true and requires snapshot permission; false skips SVG export. Inspect the PNG returned by render_techdraw_page to verify labels, leaders, frame, title block and section/detail intent.")
    def check_techdraw_conformance(page_name: str, check_rendered_text: bool = True,
            document_name: Optional[str] = None, ctx: Context = None):
        return checked_call(ctx, 'check_techdraw_conformance', require_snapshots=check_rendered_text, page_name=page_name,
            check_rendered_text=check_rendered_text, document_name=document_name)
