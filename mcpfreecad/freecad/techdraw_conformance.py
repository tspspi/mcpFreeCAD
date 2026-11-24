"""Bounded drawing-review heuristics over page and geometry readback.

These checks do not certify feature completeness, font geometry or standards.
Treat layout circles and reference absence as prompts for rendered review.
"""
import math

MAX_PAGE_VIEWS = 256
MAX_FINDINGS = 512


class _Findings(list):
    def __init__(self):
        super().__init__()
        self.counts = dict(error=0, warning=0, info=0)
        self.omitted = 0

    def append(self, finding):
        self.counts[finding['severity']] += 1
        if len(self) < MAX_FINDINGS:
            super().append(finding)
        else:
            self.omitted += 1

# ISO 5455 preferred scale ratios (enlargement 50:1..2:1, full size 1:1,
# reduction 1:2..1:1000), stored as the TechDraw Scale property's ratio value.
PREFERRED_ISO5455_SCALES = (
    0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5,
    1.0,
    2.0, 5.0, 10.0, 20.0, 50.0,
)
_SCALE_RELATIVE_TOLERANCE = 1e-6

# Generic warning clearance, not an ISO frame measurement or compliance limit.
PAGE_MARGIN_MM = 15.0
_SIZE_ROUNDING_MM = 6

# How close two dimension/annotation anchors can sit (in absolute sheet mm)
# before their text or leader lines are likely to collide. A heuristic, not a
# measurement of actual rendered text/line extent -- see unreferenced checks.
PROXIMITY_WARNING_MM = 12.0

# Numerical centre matching for an origin/reference-presence heuristic only.
# A view origin is not evidence of a design datum or adequate location control.
ORIGIN_TOLERANCE_MM = 1e-6

# Similar offsets invite visual review. Shared dimension baselines can be
# intentional; this threshold does not implement a normative spacing rule.
COLLINEAR_TOLERANCE_MM = 10.0

# The title block on this project's own installed ISO5457 "advanced" sheet
# presets (every size, portrait and landscape) is a fixed 180x48 mm rectangle,
# 10 mm in from the sheet's right and bottom edges -- measured directly from
# the installed template SVGs, identical on every size because the title
# block itself does not scale with the sheet. In TechDraw's own bottom-up
# page-coordinate convention (matching view/dimension/annotation x_mm/y_mm)
# this is x in [width - 190, width - 10], y in [10, 58]. Only applies to a
# page created from one of this project's own named presets; an explicit
# custom template's title-block position is unknown and not checked.
TITLE_BLOCK_WIDTH_MM = 180.0
TITLE_BLOCK_HEIGHT_MM = 48.0
TITLE_BLOCK_MARGIN_MM = 10.0

VISION_CHECKLIST = (
    'Line weights distinguish visible/hidden/dimension/section lines (not checkable from object data).',
    'Hatching direction and spacing are consistent across all faces of one section view.',
    'Inspect text against model/section geometry and unrelated lines. Structural helper-line '
        'checks cover only resolved axis-aligned ISO Oriented linear dimensions with estimated text bounds.',
    'Arrowheads and dimension lines read clearly at the rendered scale.',
    'A section/detail view cuts/magnifies the geometry actually intended, not a coincidental overlap '
        'with another view -- re-check visually after any placement change.',
)


def _is_preferred_scale(value):
    return any(math.isclose(value, allowed, rel_tol=_SCALE_RELATIVE_TOLERANCE) for allowed in PREFERRED_ISO5455_SCALES)


def _style_family(style):
    if style.startswith('ASME'):
        return 'asme'
    if style.startswith('ISO'):
        return 'din-iso'
    return None


def _placed_elements(page, dimensions):
    """Every dimension/annotation's absolute sheet position, for proximity
    checks. A dimension's own x_mm/y_mm are sheet mm relative to its parent
    view's centre (dimension_details()'s position_frame); resolve that
    against the parent view's own absolute position to get a comparable
    absolute point -- dimensions is dimension_details() payloads, which carry
    the parent view name(s) in 'references' (page['views']' own dimension
    entries do not). An annotation's x_mm/y_mm are already absolute."""
    views_by_name = {view['name']: view for view in page['views']}
    dimension_views = {view['name']: view for view in page['views'] if view['type'] == 'TechDraw::DrawViewDimension'}
    placed = []
    for dimension in dimensions:
        view = dimension_views.get(dimension['name'])
        parent_names = {ref['view'] for ref in dimension.get('references') or []}
        parent = views_by_name.get(next(iter(parent_names))) if len(parent_names)==1 else None
        if view is None or parent is None:
            continue
        placed.append((dimension['name'], parent['x_mm'] + view['x_mm'], parent['y_mm'] + view['y_mm']))
    for view in page['views']:
        if view['type'] == 'TechDraw::DrawViewAnnotation':
            placed.append((view['name'], view['x_mm'], view['y_mm']))
    return placed


def check_page(page, dimensions, view_extents=None, view_geometry=None, referenced_edges=None, view_vertices=None):
    """page: a page_details() payload. dimensions: dimension_details() payloads
    for every TechDraw::DrawViewDimension reported in page['views'].

    view_extents: optional {view_name: local_radius_mm} -- the farthest any of
    a view's own drawn geometry reaches from that view's own (x_mm, y_mm)
    anchor, in the view's *unscaled* local frame (same frame
    get_techdraw_view_geometry reports). Used to flag a view whose drawn
    content reaches past the sheet's printable area. Views missing from this
    mapping are skipped for that check, not assumed to fit.

    view_geometry: optional {view_name: [edge, ...]} -- that view's own
    get_techdraw_view_geometry()-shaped edge list, kind='edges'. Used with
    referenced_edges (a set of (view_name, edge_name) pairs already used by
    some dimension's References2D) to flag a distinct line length or circle
    radius in the drawing that no dimension on the sheet actually measures --
    a sign the part could not be rebuilt from this drawing alone. This is a
    heuristic over distinct sizes, not a full feature-by-feature completeness
    proof: redundant equal-length edges of one already-dimensioned feature are
    not re-flagged, but a genuinely new, undimensioned size is.

    view_vertices: optional {view_name: [vertex, ...]} -- that view's own
    get_techdraw_view_geometry()-shaped vertex list, kind='vertices'. Used
    with view_geometry and referenced_edges to additionally flag a circle
    whose centre is not on the view's own origin and whose centre vertex is
    not referenced by any dimension -- a feature with a stated size but no
    stated location."""
    if len(page['views']) > MAX_PAGE_VIEWS or len(dimensions) > MAX_PAGE_VIEWS:
        raise ValueError('Drawing checks support at most 256 views/dimensions per page')
    findings = _Findings()
    if page.get('show_frames') is True:
        findings.append(dict(check='editing_frames_visible',severity='warning',
            message='ViewFrames are enabled. Keep them while needed for editing, then disable them before final review/save/delivery.'))
    view_extents = view_extents or {}
    view_geometry = view_geometry or {}
    referenced_edges = referenced_edges or set()
    view_vertices = view_vertices or {}

    positions = {}
    for view in page['views']:
        if view['type'] not in ('TechDraw::DrawViewPart', 'TechDraw::DrawViewSection', 'TechDraw::DrawViewDetail'):
            continue
        positions.setdefault((round(view['x_mm'], 3), round(view['y_mm'], 3)), []).append(view['name'])
    for position, names in sorted(positions.items()):
        if len(names) > 1:
            findings.append(dict(check='duplicate_view_position', severity='warning', views=sorted(names),
                message=f'Views {", ".join(sorted(names))} share sheet position {position}. '
                    'Inspect the render and separate them unless this overlay is intentional.'))

    for owner, scale in [('page', page['scale'])] + [(view['name'], view['scale']) for view in page['views']]:
        if not _is_preferred_scale(scale):
            findings.append(dict(check='non_preferred_scale', severity='info', owner=owner, scale=scale,
                message=f"{owner}'s scale {scale:g}:1 is not one of the ISO 5455 preferred ratios."))

    profile = page.get('drawing_profile') or {}
    family = profile.get('standard_family')
    if family=='din-iso':
        heights={round(d['font_size_mm'],6) for d in dimensions
            if type(d.get('font_size_mm')) in (int,float) and math.isfinite(d['font_size_mm'])}
        if len(heights)>1:
            findings.append(dict(check='iso_dimension_text_height_mismatch',severity='warning',heights_mm=sorted(heights),
                message='Dimension text heights differ on this sheet. Use a consistent dimension/tolerance lettering height and inspect actual exported text.'))
    if family is not None:
        for dimension in dimensions:
            style_family = _style_family(dimension.get('style', ''))
            if style_family is not None and style_family != family:
                findings.append(dict(check='dimension_style_family_mismatch', severity='warning',
                    dimension=dimension['name'], style=dimension.get('style'), declared_family=family,
                    message=f"Dimension {dimension['name']} uses {dimension.get('style')!r} on a sheet "
                        f"declared {family!r}; mixed conventions on one drawing confuse a reader."))

    template = page.get('template') or {}
    editable_texts = template.get('editable_texts') or {}
    general_tolerance_stated = bool(editable_texts.get('general_tolerances'))
    if general_tolerance_stated and any(not d.get('arbitrary') and not d.get('theoretical_exact') and
            d.get('upper_tolerance')==0 and d.get('lower_tolerance')==0 for d in dimensions):
        findings.append(dict(check='general_tolerance_scope_unverified',severity='warning',
            message='A general-tolerance note is present, but its applicability to each size, location distance, angle and other specification has not been established. Review the governing edition and feature-level scope; text in this field is not proof of tolerance coverage.'))
    if not general_tolerance_stated:
        for dimension in dimensions:
            if (not dimension.get('arbitrary') and not dimension.get('theoretical_exact') and dimension.get('upper_tolerance') == 0 and
                    dimension.get('lower_tolerance') == 0):
                findings.append(dict(check='undocumented_tolerance', severity='warning',
                    dimension=dimension['name'],
                    message=f"Dimension {dimension['name']} has no explicit tolerance and the sheet's "
                        "general_tolerances field is blank; its precision class is undefined."))

    for view in page['views']:
        if view['type'] == 'TechDraw::DrawViewSection' and not view.get('section_symbol'):
            findings.append(dict(check='missing_section_symbol', severity='info', view=view['name'],
                message=f"Section view {view['name']} has no SectionSymbol label."))
    if family=='din-iso':
        import re
        sections=[v for v in page['views'] if v['type']=='TechDraw::DrawViewSection']
        for view in sections:
            symbol=view.get('section_symbol')
            if not symbol:continue
            label=re.compile(r'(?<![A-Za-z0-9])'+re.escape(symbol)+r'\s*[-–—]\s*'+re.escape(symbol)+r'(?![A-Za-z0-9])')
            captions=[v for v in page['views'] if v['type']=='TechDraw::DrawViewAnnotation' and
                label.search(' '.join(v.get('text') or []))]
            if not label.search(view.get('caption','')) and not captions:
                findings.append(dict(check='section_identification_unverified',severity='warning',view=view['name'],
                    message='Section identification was not found in the view caption or page annotations. Add and visually verify its matching identification above the section; cutting-plane letters alone do not identify the resulting view.'))
            elif captions and all(v['y_mm']<=view['y_mm'] for v in captions):
                findings.append(dict(check='section_identification_below_view',severity='warning',view=view['name'],
                    message='The matching section annotation is at or below its view centre. Position the identification above the section and inspect clearance in the image.'))
            if sum(v.get('section_symbol')==symbol for v in sections)>1:
                findings.append(dict(check='duplicate_section_identification',severity='warning',view=view['name'],
                    message='Multiple sections use the same identification. Resolve ambiguous cutting-plane/view references.'))

    template_width = template.get('width_mm')
    template_height = template.get('height_mm')
    if template_width is not None and template_height is not None:
        for view in page['views']:
            radius = view_extents.get(view['name'])
            if radius is None:
                continue
            reach = radius * view['scale']
            low_x, high_x = view['x_mm'] - reach, view['x_mm'] + reach
            low_y, high_y = view['y_mm'] - reach, view['y_mm'] + reach
            if (low_x < PAGE_MARGIN_MM or high_x > template_width - PAGE_MARGIN_MM or
                    low_y < PAGE_MARGIN_MM or high_y > template_height - PAGE_MARGIN_MM):
                findings.append(dict(check='view_extends_past_sheet_edge', severity='warning', view=view['name'],
                        message=f"{view['name']}'s estimated bounding circle reaches roughly {low_x:.0f}..{high_x:.0f} mm "
                        f"horizontally and {low_y:.0f}..{high_y:.0f} mm vertically on a "
                        f"{template_width:g}x{template_height:g} mm sheet; inspect the actual outline and frame."))

        if profile.get('preset') is not None:
            title_x_low = template_width - TITLE_BLOCK_MARGIN_MM - TITLE_BLOCK_WIDTH_MM
            title_x_high = template_width - TITLE_BLOCK_MARGIN_MM
            title_y_low = TITLE_BLOCK_MARGIN_MM
            title_y_high = TITLE_BLOCK_MARGIN_MM + TITLE_BLOCK_HEIGHT_MM
            for view in page['views']:
                radius = view_extents.get(view['name'])
                if radius is None:
                    continue
                reach = radius * view['scale']
                closest_x = min(max(view['x_mm'], title_x_low), title_x_high)
                closest_y = min(max(view['y_mm'], title_y_low), title_y_high)
                if math.hypot(view['x_mm'] - closest_x, view['y_mm'] - closest_y) < reach:
                    findings.append(dict(check='view_overlaps_title_block', severity='warning', view=view['name'],
                        message=f"{view['name']}'s estimated bounding circle intersects the assumed preset title-block "
                            f"area (x >= {title_x_low:g} mm, y <= {title_y_high:g} mm); move it or reduce its "
                            "scale if the rendered content actually overlaps. Verify the template layout."))

    size_references = referenced_edges
    if dimensions:
        size_references = set()
        for dimension in dimensions:
            if dimension.get('arbitrary'):
                continue
            kind = dimension.get('type')
            for ref in dimension.get('references') or []:
                names = ref.get('names') or []
                if len(names) != 1:
                    continue
                edge = next((edge for edge in view_geometry.get(ref['view'],[]) if edge['name']==names[0]),None)
                if edge is None:
                    continue
                measured = kind in ('Radius','Diameter') and edge.get('curve')=='Circle'
                if edge.get('curve')=='Line' and kind in ('Distance','DistanceX','DistanceY'):
                    measured = kind=='Distance'
                    points = edge.get('vertices_mm') or []
                    if len(points)==2 and kind in ('DistanceX','DistanceY'):
                        other = 1 if kind=='DistanceX' else 0
                        measured = abs(points[0][other]-points[1][other]) <= 1e-6
                if measured:
                    size_references.add((ref['view'],names[0]))
    for view_name, edges in view_geometry.items():
        by_size = {}
        for edge in edges:
            curve = edge.get('curve')
            if curve not in ('Circle', 'Line'):
                continue
            size = edge.get('radius_mm') if curve == 'Circle' else edge.get('length_mm')
            if size is None:
                continue
            by_size.setdefault((curve, round(size, _SIZE_ROUNDING_MM)), []).append(edge['name'])
        for (curve, size), names in sorted(by_size.items()):
            if not any((view_name, name) in size_references for name in names):
                findings.append(dict(check='unreferenced_feature_size', severity='warning', view=view_name,
                    size_mm=size, edges=names,
                    message=f"{view_name} has a feature of size {size:g} mm ({', '.join(names)}) that no "
                        "dimension directly references in this view. Review the feature's size coverage "
                        "across all views; do not add a redundant dimension just to clear this warning."))

    for view_name, edges in view_geometry.items():
        vertices = view_vertices.get(view_name) or []
        seen_circles=set()
        for edge in edges:
            if edge.get('curve') != 'Circle':
                continue
            center = edge.get('center_mm') or [0.0, 0.0]
            key=(round(center[0],6),round(center[1],6),round(edge.get('radius_mm') or 0.,6))
            if key in seen_circles:continue
            seen_circles.add(key)
            if math.hypot(center[0], center[1]) <= ORIGIN_TOLERANCE_MM:
                continue  # Origin heuristic only; datum/location adequacy is not assessed.
            matches = [vertex['name'] for vertex in vertices if math.hypot(
                vertex['point_mm'][0] - center[0], vertex['point_mm'][1] - center[1]) <= ORIGIN_TOLERANCE_MM]
            if not matches:
                continue  # no selectable centre vertex found for this circle; cannot check
            if not any((view_name,name) in referenced_edges for name in matches):
                findings.append(dict(check='unreferenced_feature_position', severity='warning', view=view_name,
                    edge=edge['name'], center_mm=[center[0], center[1]],
                    message=f"{view_name}'s circle {edge['name']} (centre {center[0]:g}, {center[1]:g}) is "
                        "not on this view's own origin, and no dimension references its centre vertex "
                        f"({', '.join(matches)}). Review its datum-based location dimensions across the sheet."))

    views_by_name = {view['name']: view for view in page['views']}
    dimension_views = {view['name']: view for view in page['views'] if view['type'] == 'TechDraw::DrawViewDimension'}
    for dimension in dimensions:
        view = dimension_views.get(dimension['name'])
        parent_names = {ref['view'] for ref in dimension.get('references') or []}
        parent_name = next(iter(parent_names)) if len(parent_names)==1 else None
        parent = views_by_name.get(parent_name)
        radius = view_extents.get(parent_name) if parent is not None else None
        if view is None or parent is None or radius is None:
            continue
        reach = math.hypot(view['x_mm'], view['y_mm'])
        silhouette = radius * parent['scale']
        if reach < silhouette:
            findings.append(dict(check='dimension_inside_view_silhouette', severity='warning',
                dimension=dimension['name'], view=parent_name,
                message=f"{dimension['name']}'s anchor lies within {parent_name}'s estimated bounding circle "
                    f"({reach:.0f} mm from its centre, radius about {silhouette:.0f} mm). "
                    "Inspect actual text, leader and outline before repositioning."))

    placed = _placed_elements(page, dimensions)
    for i, (name_a, x_a, y_a) in enumerate(placed):
        for name_b, x_b, y_b in placed[i + 1:]:
            distance = math.hypot(x_a - x_b, y_a - y_b)
            if distance < PROXIMITY_WARNING_MM:
                findings.append(dict(check='elements_placed_close_together', severity='info',
                    elements=sorted([name_a, name_b]), distance_mm=round(distance, 1),
                    message=f"{name_a} and {name_b} are only {distance:.1f} mm apart (anchor to anchor); "
                        "their text or leader lines may cross. Checked by anchor distance only, not actual "
                        "rendered text/line extent -- confirm by rendering."))

    by_parent_view = {}
    for dimension in dimensions:
        view = dimension_views.get(dimension['name'])
        kind = dimension.get('type')
        if kind not in ('DistanceX','DistanceY'):
            continue
        parent_names = {ref['view'] for ref in dimension.get('references') or []}
        parent_name = next(iter(parent_names)) if len(parent_names)==1 else None
        if view is None or parent_name is None:
            continue
        by_parent_view.setdefault((parent_name,kind), []).append((dimension['name'], view['x_mm'], view['y_mm']))
    for (parent_name,kind), entries in by_parent_view.items():
        for i, (name_a, x_a, y_a) in enumerate(entries):
            for name_b, x_b, y_b in entries[i + 1:]:
                same_x, same_y = abs(x_a - x_b) < COLLINEAR_TOLERANCE_MM, abs(y_a - y_b) < COLLINEAR_TOLERANCE_MM
                aligned = same_y if kind=='DistanceX' else same_x
                other_axis_gap = abs(x_a-x_b) if kind=='DistanceX' else abs(y_a-y_b)
                if aligned and not (same_x and same_y) and other_axis_gap > PROXIMITY_WARNING_MM:
                    findings.append(dict(check='dimensions_share_offset_line', severity='warning',
                        dimensions=sorted([name_a, name_b]), view=parent_name,
                        message=f"{name_a} and {name_b} are {kind} dimensions from {parent_name} "
                            "at similar offsets. Inspect their spans and labels; a shared baseline may be "
                            "intentional, so stagger only if it improves readability."))

    from . import techdraw_dimension_layout, techdraw_measurement_checks
    measurement_checks = techdraw_measurement_checks.check_measurements(dimensions,view_geometry,view_vertices,findings.append)
    dimension_layout = techdraw_dimension_layout.check_layout(page,dimensions,view_geometry,view_vertices,findings.append)
    return dict(
        dimension_layout=dimension_layout,measurement_checks=measurement_checks,
        conformance_certified=False,
        manufacturing_readiness='not_assessed',
        projected_view_count=sum(view['type'] in ('TechDraw::DrawViewPart','TechDraw::DrawViewSection','TechDraw::DrawViewDetail') for view in page['views']),
        measured_dimension_count=len(dimensions),
        standard_family=family,
        findings=findings,
        error_count=findings.counts['error'],
        warning_count=findings.counts['warning'],
        info_count=findings.counts['info'],
        findings_omitted=findings.omitted,
        feature_completeness_certified=False,
        datum_location_assessed=False,
        layout_bounds_estimated=True,
        blank_title_block_fields=sorted(key for key, value in editable_texts.items() if not value),
        vision_checklist=list(VISION_CHECKLIST),
    )
