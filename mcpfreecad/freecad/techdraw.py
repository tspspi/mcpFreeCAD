"""TechDraw page operations and bounded full-sheet visual feedback.

FreeCAD/Qt imports stay inside operations. PNG bytes cross the bridge directly;
clients never need to open a path on the FreeCAD host.
"""
import base64
from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time

COMMANDS = frozenset({'list_techdraw_presets', 'list_techdraw_templates', 'list_techdraw_pages',
    'get_techdraw_page', 'create_techdraw_page', 'update_techdraw_page', 'create_techdraw_view', 'update_techdraw_view',
    'create_techdraw_section_view', 'create_techdraw_detail_view', 'set_techdraw_view_frames',
    'render_techdraw_page', 'export_techdraw_drawing', 'check_techdraw_conformance', 'create_techdraw_hole_pattern'})
MAX_SVG_BYTES = 4 * 1024 * 1024
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 8 * 1024 * 1024
EXPORT_FORMATS = ('svg', 'pdf')
SECTION_DIRECTIONS = ('Right', 'Left', 'Up', 'Down')


def _qt_font_metrics(text, size, style):
    """Measure SVG ink bounds using the GUI font database; no Qt import headless."""
    from PySide import QtGui
    family = style.get('font-family','sans-serif').split(',')[0].strip().strip('\"\'')
    if len(family)>256 or not 0<size<=16384:
        raise ValueError('Unsupported font metrics input')
    font = QtGui.QFont(family)
    font.setPixelSize(max(1,round(size*64)))
    font.setHintingPreference(QtGui.QFont.PreferNoHinting)
    font.setItalic(style.get('font-style') in ('italic','oblique'))
    weight = style.get('font-weight','normal')
    font.setBold(weight in ('bold','bolder') or (weight.isdigit() and int(weight)>=600))
    metrics = QtGui.QFontMetricsF(font)
    rect = metrics.tightBoundingRect(text)
    return tuple(value/64. for value in (metrics.horizontalAdvance(text),
        rect.left(),rect.top(),rect.right(),rect.bottom()))


def image_size(width, height):
    if (type(width) is not int or type(height) is not int or
            not 128 <= width <= 4096 or not 128 <= height <= 4096 or width * height > MAX_PIXELS):
        raise ValueError('Preview size must be 128..4096 pixels per side and at most 8 megapixels')
    return width, height


def _number(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('Invalid ' + name)
    return float(value)


def _name(document, value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', value) is None:
        raise ValueError('Use a unique object name of at most 80 ASCII letters, digits or underscores')
    if document.getObject(value) is not None:
        raise ValueError('Object name already exists: ' + value)
    return value


def _page(document, name):
    page = document.getObject(name)
    if page is None or page.TypeId != 'TechDraw::DrawPage':
        raise ValueError('Unknown TechDraw page: ' + str(name))
    return page


def _existing_view(document, page, name):
    view = document.getObject(name)
    if view is None or not any(item.Name == view.Name for item in page.getAllViews()):
        raise ValueError('Choose an existing view on this drawing page')
    return view


def _base_part_view(document, page, name, allow_section=False):
    view = document.getObject(name)
    allowed=('TechDraw::DrawViewPart','TechDraw::DrawViewSection') if allow_section else ('TechDraw::DrawViewPart',)
    if (view is None or view.TypeId not in allowed or
            not any(item.Name == view.Name for item in page.getAllViews())):
        raise ValueError('Choose a projected part view on this drawing page as the section/detail base')
    return view


def _vector3(value, name):
    if type(value) not in (tuple, list) or len(value) != 3 or any(
            type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError(name + ' requires three finite coordinates')
    return [float(x) for x in value]


def _template_root(App):
    return (Path(App.getResourceDir()) / 'Mod' / 'TechDraw' / 'Templates').resolve()


def _template_path(App, name):
    if type(name) is not str or not name or len(name) > 512:
        raise ValueError('Invalid installed template name')
    root = _template_root(App)
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != '.svg' or not path.is_file():
        raise ValueError('Choose an installed SVG template from list_techdraw_templates')
    return path


@contextmanager
def _edit(document, label):
    document.openTransaction(label)
    try:
        yield
        document.recompute()
    except BaseException:
        document.abortTransaction()
        raise
    else:
        document.commitTransaction()


def page_details(document, page):
    from . import techdraw_presets
    template = page.Template
    views = []
    for view in page.Views:
        entry = dict(name=view.Name, label=view.Label, type=view.TypeId,
            x_mm=float(view.X), y_mm=float(view.Y), scale=float(view.Scale),
            rotation_deg=float(getattr(view,'Rotation',0.)), state=list(view.State))
        if hasattr(view, 'Source'):
            entry['sources'] = [obj.Name for obj in view.Source]
        if hasattr(view, 'Direction'):
            entry['direction'] = list(view.Direction)
        if hasattr(view, 'BaseView') and view.BaseView is not None:
            entry['base_view'] = view.BaseView.Name
        if hasattr(view, 'SectionNormal'):
            entry['section_normal'] = list(view.SectionNormal)
            entry['section_origin'] = list(view.SectionOrigin)
            entry['section_direction'] = str(view.SectionDirection)
            entry['section_symbol'] = str(getattr(view, 'SectionSymbol', '') or '')
        if hasattr(view, 'AnchorPoint'):
            entry['anchor_point_mm'] = list(view.AnchorPoint)
            entry['anchor_point_frame'] = 'unscaled_projected_base_view_mm'
            entry['radius_mm'] = float(view.Radius)
        entry['view_preset']=getattr(view,'MCPViewPreset',None)
        entry['projection_kind']=getattr(view,'MCPProjectionKind',None)
        entry['caption']=str(getattr(view,'Caption','') or '')
        if view.TypeId=='TechDraw::DrawViewAnnotation':
            entry['text']=list(view.Text)
        views.append(entry)
    return dict(document=document.Name, page=page.Name, label=page.Label,
        scale=float(page.Scale), show_frames=bool(getattr(getattr(page,'ViewObject',None),'ShowFrames',False)), state=list(page.State), views=views, drawing_profile=techdraw_presets.profile_details(page),
        template=None if template is None else dict(name=template.Name,
            width_mm=float(template.Width), height_mm=float(template.Height),
            editable_texts=dict(getattr(template, 'EditableTexts', {}))))


def _wait_for_projection(QtCore, deadline):
    # TechDraw runs hidden-line removal and face extraction in QtConcurrent.
    # A recomputed document can still have the preceding projection in its GUI.
    # Drain completion callbacks as well as the workers they may start. A busy
    # shared pool causes a bounded refusal rather than a misleading old image.
    pool = QtCore.QThreadPool.globalInstance()
    idle = 0
    while idle < 2:
        if time.monotonic() >= deadline:
            raise RuntimeError('TechDraw projection is still updating; retry the preview')
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        finished = pool.waitForDone(10)
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        idle = idle + 1 if finished and pool.activeThreadCount() == 0 else 0


def render_page(document, page, Gui, width, height):
    image_size(width, height)
    if Gui is None:
        raise RuntimeError('Full-sheet TechDraw rendering requires the FreeCAD GUI')
    import TechDrawGui
    from PySide import QtCore, QtGui, QtSvg
    deadline = time.monotonic() + 10.
    # Complete any older projection before asking for the current model;
    # TechDraw skips starting a replacement while its older HLR is running.
    _wait_for_projection(QtCore, deadline)
    document.recompute()
    page.ViewObject.show()
    page.requestPaint()
    _wait_for_projection(QtCore, deadline)
    Gui.updateGui()
    with TemporaryDirectory(prefix='mcpfreecad-techdraw-') as folder:
        path = Path(folder) / 'page.svg'
        TechDrawGui.exportPageAsSvg(page, str(path))
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_SVG_BYTES:
            raise ValueError('Drawing SVG is missing or exceeds the 4 MiB preview limit')
        svg = path.read_bytes()
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg))
    if not renderer.isValid():
        raise ValueError('FreeCAD exported an invalid drawing SVG')
    bounds = renderer.viewBoxF()
    if bounds.width() <= 0 or bounds.height() <= 0:
        raise ValueError('Drawing SVG has no page bounds')
    image = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
    if image.isNull():
        raise MemoryError('Unable to allocate drawing preview')
    image.fill(QtGui.QColor('white'))
    factor = min(width / bounds.width(), height / bounds.height())
    w, h = bounds.width() * factor, bounds.height() * factor
    painter = QtGui.QPainter(image)
    try:
        renderer.render(painter, QtCore.QRectF((width-w)/2, (height-h)/2, w, h))
    finally:
        painter.end()
    output = QtCore.QBuffer()
    output.open(QtCore.QIODevice.WriteOnly)
    if not image.save(output, 'PNG'):
        raise RuntimeError('Unable to encode drawing preview')
    png = bytes(output.data())
    if len(png) > MAX_PNG_BYTES:
        raise ValueError('Drawing PNG exceeds the 8 MiB preview limit')
    return dict(page_details(document, page), mime_type='image/png', width=width, height=height,
        byte_length=len(png), sha256=hashlib.sha256(png).hexdigest(),
        svg_sha256=hashlib.sha256(svg).hexdigest(), data_base64=base64.b64encode(png).decode('ascii'))


def export_drawing(document, page, Gui, export_format):
    if export_format not in EXPORT_FORMATS:
        raise ValueError('Choose an export format: svg or pdf')
    if Gui is None:
        raise RuntimeError('TechDraw export requires the FreeCAD GUI')
    import TechDrawGui
    from PySide import QtCore
    deadline = time.monotonic() + 10.
    _wait_for_projection(QtCore, deadline)
    document.recompute()
    page.ViewObject.show()
    page.requestPaint()
    _wait_for_projection(QtCore, deadline)
    Gui.updateGui()
    with TemporaryDirectory(prefix='mcpfreecad-techdraw-export-') as folder:
        path = Path(folder) / ('page.' + export_format)
        if export_format == 'svg':
            TechDrawGui.exportPageAsSvg(page, str(path))
            mime_type, max_bytes = 'image/svg+xml', MAX_SVG_BYTES
        else:
            TechDrawGui.exportPageAsPdf(page, str(path))
            mime_type, max_bytes = 'application/pdf', MAX_PDF_BYTES
        if not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
            raise ValueError(f'Drawing {export_format.upper()} export is missing or exceeds its size limit')
        data = path.read_bytes()
    return dict(document=document.Name, page=page.Name, format=export_format, mime_type=mime_type,
        byte_length=len(data), sha256=hashlib.sha256(data).hexdigest(),
        data_base64=base64.b64encode(data).decode('ascii'))


def execute(command, params, App, Gui, document=None):
    from . import techdraw_dimensions
    if command in techdraw_dimensions.COMMANDS:
        return techdraw_dimensions.execute(command, params, App, Gui, document)
    if command not in COMMANDS:
        raise ValueError('Unknown TechDraw command')
    if command=='create_techdraw_hole_pattern':
        from . import techdraw_hole_patterns
        return techdraw_hole_patterns.execute(params,App,Gui,document)
    import TechDraw  # Registers the FreeCAD document types.
    from . import techdraw_presets
    if command == 'list_techdraw_presets':
        return techdraw_presets.catalog(App)
    if command == 'list_techdraw_templates':
        root = _template_root(App)
        return {'templates': sorted(str(path.relative_to(root)) for path in root.rglob('*.svg')
            if path.resolve().is_relative_to(root))}
    if command == 'list_techdraw_pages':
        return {'document': document.Name, 'pages': [page_details(document, obj)
            for obj in document.Objects if obj.TypeId == 'TechDraw::DrawPage']}
    if command == 'create_techdraw_page':
        name = _name(document, params.get('name', 'DrawingPage'))
        template_path,preset = techdraw_presets.sheet_choice(App,params.get('template'),params.get('preset'))
        scale = _number(params.get('scale', 1.), 'page scale', positive=True)
        texts = params.get('editable_texts')
        if texts is None:
            texts = {}
        if type(texts) is not dict or any(type(k) is not str or type(v) is not str for k, v in texts.items()):
            raise ValueError('Editable template texts must map field names to strings')
        with _edit(document, 'Create TechDraw page'):
            page = document.addObject('TechDraw::DrawPage', name)
            template = document.addObject('TechDraw::DrawSVGTemplate', name + 'Template')
            template.Template = str(template_path)
            page.Template = template
            unknown = set(texts) - set(template.EditableTexts)
            if unknown:
                raise ValueError('Unknown editable template fields: ' + ', '.join(sorted(unknown)))
            page.Scale = scale
            techdraw_presets.apply_sheet(document,page,template,preset,scale,texts)
        return page_details(document, page)
    page = _page(document, params.get('page_name'))
    if command == 'get_techdraw_page':
        return page_details(document, page)
    if command == 'set_techdraw_view_frames':
        enabled = params.get('enabled')
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        if Gui is None or not hasattr(page.ViewObject,'ShowFrames'):
            raise RuntimeError('TechDraw view frames require the FreeCAD GUI')
        with _edit(document,'Set TechDraw view frames'):
            page.ViewObject.ShowFrames = enabled
        return page_details(document,page)
    if command == 'update_techdraw_page':
        texts = params.get('editable_texts')
        if (type(texts) is not dict or not 1 <= len(texts) <= 64 or
                any(type(k) is not str or type(v) is not str or len(k)>128 or len(v)>1024 for k,v in texts.items())):
            raise ValueError('Provide 1..64 template fields, each with a string of at most 1024 characters')
        template = page.Template
        if template is None:
            raise ValueError('Drawing page has no editable template')
        unknown = set(texts)-set(template.EditableTexts)
        if unknown:
            raise ValueError('Unknown editable template fields: '+', '.join(sorted(unknown)))
        with _edit(document,'Update TechDraw title fields'):
            template.EditableTexts = dict(template.EditableTexts,**texts)
            if 'sheet_number' in texts and hasattr(page,'MCPSheetNumberAutomatic'):
                page.MCPSheetNumberAutomatic = False
        return page_details(document,page)
    if command == 'render_techdraw_page':
        if not getattr(App, 'GuiUp', False):
            raise RuntimeError('Full-sheet TechDraw rendering requires the FreeCAD GUI')
        return render_page(document, page, Gui, params.get('width', 1600), params.get('height', 1200))
    if command == 'create_techdraw_view':
        name = _name(document, params.get('name', 'DrawingView'))
        sources = params.get('source_names')
        if type(sources) is not list or not 1 <= len(sources) <= 64 or any(type(x) is not str for x in sources):
            raise ValueError('Choose 1..64 source object names in this document')
        objects = [document.getObject(value) for value in sources]
        if any(obj is None or not hasattr(obj, 'Shape') or obj.Shape.isNull() for obj in objects):
            raise ValueError('Every view source must have a nonempty shape')
        direction,x_direction,view_preset = techdraw_presets.view_choice(
            params.get('view_preset'),params.get('direction'),params.get('x_direction'))
        x = _number(params.get('x_mm', 100.), 'view X')
        y = _number(params.get('y_mm', 120.), 'view Y')
        requested_scale = params.get('scale')
        scale = _number(float(page.Scale) if requested_scale is None else requested_scale, 'view scale', positive=True)
        with _edit(document, 'Create TechDraw view'):
            view = document.addObject('TechDraw::DrawViewPart', name)
            view.Source = objects
            view.Direction = App.Vector(*direction)
            if x_direction is not None:view.XDirection=App.Vector(*x_direction)
            if view_preset is not None:
                view.addProperty('App::PropertyString','MCPViewPreset','MCP TechDraw');view.MCPViewPreset=view_preset
                view.addProperty('App::PropertyString','MCPProjectionKind','MCP TechDraw')
                view.MCPProjectionKind='axonometric' if view_preset=='isometric' else 'orthographic'
            view.ScaleType = 'Page' if requested_scale is None else 'Custom'
            view.Scale = scale
            page.addView(view)
            # addView centres a newly inserted view on the sheet.
            view.X, view.Y = x, y
        return page_details(document, page)
    if command == 'update_techdraw_view':
        view = _existing_view(document, page, params.get('view_name'))
        direction_requested = (params.get('direction') is not None or params.get('x_direction') is not None or
            params.get('view_preset') is not None)
        if direction_requested and view.TypeId != 'TechDraw::DrawViewPart':
            raise ValueError('Only a plain projected part view accepts a new projection direction')
        x = None if params.get('x_mm') is None else _number(params.get('x_mm'), 'view X')
        y = None if params.get('y_mm') is None else _number(params.get('y_mm'), 'view Y')
        requested_scale = params.get('scale')
        scale = None if requested_scale is None else _number(requested_scale, 'view scale', positive=True)
        if x is None and y is None and scale is None and not direction_requested:
            raise ValueError('Provide at least one view field to update')
        direction = x_direction = view_preset = None
        if direction_requested:
            direction, x_direction, view_preset = techdraw_presets.view_choice(
                params.get('view_preset'), params.get('direction'), params.get('x_direction'))
        with _edit(document, 'Update TechDraw view'):
            if x is not None: view.X = x
            if y is not None: view.Y = y
            if scale is not None: view.ScaleType = 'Custom'; view.Scale = scale
            if direction_requested:
                view.Direction = App.Vector(*direction)
                if x_direction is not None: view.XDirection = App.Vector(*x_direction)
                for property_name in ('MCPViewPreset','MCPProjectionKind'):
                    if not hasattr(view, property_name):
                        view.addProperty('App::PropertyString',property_name,'MCP TechDraw')
                view.MCPViewPreset = view_preset or ''
                view.MCPProjectionKind = ('axonometric' if view_preset == 'isometric' else
                    'orthographic' if view_preset is not None else '')
        return page_details(document, page)
    if command == 'create_techdraw_section_view':
        base_view = _base_part_view(document, page, params.get('base_view_name'))
        name = _name(document, params.get('name', 'DrawingSection'))
        normal = techdraw_presets._unit_vector(params.get('section_normal'))
        origin = _vector3(params.get('section_origin'), 'Section origin')
        section_direction = params.get('section_direction', 'Right')
        if section_direction not in SECTION_DIRECTIONS:
            raise ValueError('Section direction must be one of Right/Left/Up/Down')
        symbol = params.get('symbol')
        if symbol is not None and (type(symbol) is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,3}', symbol) is None):
            raise ValueError('Section symbol must be 1..4 letters/digits starting with a letter')
        x_param, y_param = params.get('x_mm'), params.get('y_mm')
        x = min(float(base_view.X)+60.,float(page.Template.Width)-20.) if x_param is None else _number(x_param, 'view X')
        y = float(base_view.Y) if y_param is None else _number(y_param, 'view Y')
        requested_scale = params.get('scale')
        scale = _number(float(page.Scale) if requested_scale is None else requested_scale, 'view scale', positive=True)
        with _edit(document, 'Create TechDraw section view'):
            view = document.addObject('TechDraw::DrawViewSection', name)
            view.BaseView = base_view
            view.Source = list(base_view.Source)
            view.SectionNormal = App.Vector(*normal)
            view.SectionOrigin = App.Vector(*origin)
            view.SectionDirection = section_direction
            if symbol is not None and hasattr(view, 'SectionSymbol'):
                view.SectionSymbol = symbol
            view.ScaleType = 'Page' if requested_scale is None else 'Custom'
            view.Scale = scale
            page.addView(view)
            view.X, view.Y = x, y
        return page_details(document, page)
    if command == 'create_techdraw_detail_view':
        base_view = _base_part_view(document, page, params.get('base_view_name'),allow_section=True)
        name = _name(document, params.get('name', 'DrawingDetail'))
        anchor = params.get('anchor_point_mm')
        if type(anchor) not in (tuple, list) or len(anchor) not in (2, 3) or any(
                type(x) not in (int, float) or not math.isfinite(x) for x in anchor):
            raise ValueError('Detail anchor point requires 2 or 3 finite unscaled base-view millimetres')
        anchor = [float(anchor[0]), float(anchor[1]), float(anchor[2]) if len(anchor) == 3 else 0.]
        if anchor[2] != 0.:
            raise ValueError('A projected detail anchor must have zero Z')
        radius = _number(params.get('radius_mm'), 'detail radius', positive=True)
        x_param, y_param = params.get('x_mm'), params.get('y_mm')
        # Defaults are suggested anchors, not an automatic layout or fit proof.
        x = min(float(base_view.X)+60.,float(page.Template.Width)-20.) if x_param is None else _number(x_param, 'view X')
        y = max(20.,float(base_view.Y)-60.) if y_param is None else _number(y_param, 'view Y')
        scale = _number(params.get('scale', 2.), 'detail scale', positive=True)
        with _edit(document, 'Create TechDraw detail view'):
            view = document.addObject('TechDraw::DrawViewDetail', name)
            view.BaseView = base_view
            view.Source = list(base_view.Source)
            # FreeCAD clips the already-rotated base source. Selection
            # coordinates and AnchorPoint therefore use the same frame.
            view.AnchorPoint = App.Vector(*anchor)
            view.Radius = radius
            view.ScaleType = 'Custom'
            view.Scale = scale
            page.addView(view)
            view.X, view.Y = x, y
        return page_details(document, page)
    if command == 'export_techdraw_drawing':
        if not getattr(App, 'GuiUp', False):
            raise RuntimeError('TechDraw export requires the FreeCAD GUI')
        return export_drawing(document, page, Gui, params.get('format', 'svg'))
    if command != 'check_techdraw_conformance':
        raise ValueError('Unknown TechDraw command')
    from . import techdraw_conformance, techdraw_svg_overlap
    if len(page.getAllViews()) > techdraw_conformance.MAX_PAGE_VIEWS:
        raise ValueError('Drawing checks support at most 256 views/dimensions per page')
    requested_render = params.get('check_rendered_text', True)
    if type(requested_render) is not bool:
        raise ValueError('check_rendered_text must be a boolean')
    gui_available = Gui is not None and bool(getattr(App, 'GuiUp', False))
    if gui_available:
        techdraw_dimensions.prepare(document, page, Gui)
    # Read values after projection completion, not from the preceding revision.
    payload = page_details(document, page)
    dimensions = []; invalid = []
    referenced_edges = set()
    view_extents = {}; view_geometry = {}; view_vertices = {}; coverage = {}
    remaining = 4096
    for entry in payload['views']:
        obj = document.getObject(entry['name'])
        if entry['type'] == 'TechDraw::DrawViewDimension':
            try:
                dimensions.append(techdraw_dimensions.dimension_details(obj))
                for ref_view, names in obj.References2D:
                    if ref_view is not None:
                        referenced_edges.update((ref_view.Name, name) for name in names)
            except (ValueError, RuntimeError, AttributeError):
                invalid.append(dict(check='invalid_dimension',severity='error',dimension=entry['name'],
                    message='Dimension value or references could not be read. Repair its geometry references and recheck.'))
        elif entry['type'] in ('TechDraw::DrawViewPart', 'TechDraw::DrawViewSection', 'TechDraw::DrawViewDetail'):
            status = dict(edges_complete=False,vertices_complete=False)
            coverage[entry['name']] = status
            if not gui_available:
                status['reason'] = 'gui_unavailable'; continue
            for kind, inventory in (('edges',view_geometry),('vertices',view_vertices)):
                rows = []; offset = 0; complete = False
                while remaining:
                    result = techdraw_dimensions.geometry_page(document,page,obj,kind,offset,min(256,remaining))
                    if result['has_more'] and not result['returned']:
                        raise ValueError('TechDraw geometry paging did not advance')
                    rows.extend(result['geometry']); remaining -= result['returned']
                    if not result['has_more']:
                        complete = True; break
                    offset = result['next_offset']
                inventory[entry['name']] = rows
                status[kind+'_complete'] = complete
                if not complete: status['reason'] = 'geometry_entry_limit'
            radius = 0.
            for edge in view_geometry.get(entry['name'],[]):
                if edge.get('curve') == 'Circle':
                    center = edge.get('center_mm') or [0.,0.]
                    radius = max(radius,math.hypot(center[0],center[1])+float(edge.get('radius_mm',0.)))
                else:
                    for vertex in edge.get('vertices_mm') or []:
                        radius = max(radius,math.hypot(vertex[0],vertex[1]))
            if status['edges_complete'] and radius:
                view_extents[entry['name']] = radius
    from . import techdraw_hole_patterns
    patterns=[]
    for entry in payload['views']:
        obj=document.getObject(entry['name'])
        if getattr(obj,'MCPHolePatterns',''):
            try:patterns.extend(techdraw_hole_patterns.check(obj,view_geometry.get(obj.Name,[]),invalid.append))
            except (ValueError,TypeError,KeyError):
                invalid.append(dict(check='hole_pattern_metadata_invalid',severity='error',view=obj.Name,
                    message='Hole pattern metadata is unreadable or exceeds the bounded contract.'))
    report = techdraw_conformance.check_page(payload,dimensions,view_extents=view_extents,
        view_geometry=view_geometry,referenced_edges=referenced_edges,view_vertices=view_vertices)
    rendered = dict(requested=requested_render,performed=False,coverage_complete=False)
    extra = invalid
    if requested_render and gui_available:
        svg = export_drawing(document,page,Gui,'svg')
        rendered = dict(techdraw_svg_overlap.analyze_svg(base64.b64decode(svg['data_base64']),font_metrics=_qt_font_metrics),
            requested=True,performed=True,svg_sha256=svg['sha256'])
        extra += rendered.pop('findings')
        if (payload.get('drawing_profile') or {}).get('standard_family')=='din-iso':
            labels=rendered.get('decimal_point_labels',[])
            if labels:
                extra.append(dict(check='iso_decimal_marker_review',severity='warning',texts=labels,
                    message='Numeric SVG labels contain decimal points. ISO 129-1:2018 calls for decimal commas. Identify dimension/tolerance labels and correct their presentation; identifiers and other text may be exceptions to this numeric-label heuristic.'))
    else:
        rendered['reason'] = 'gui_unavailable' if requested_render else 'not_requested'
    for finding in extra:
        report[finding['severity']+'_count'] += 1
        if len(report['findings']) < techdraw_conformance.MAX_FINDINGS:
            report['findings'].append(finding)
        else:
            report['findings_omitted'] += 1
    report.update(hole_patterns=patterns,geometry_coverage=coverage,geometry_entries_checked=4096-remaining,
        unsupported_view_types=sorted({row['type'] for row in payload['views'] if row['type'] not in
            ('TechDraw::DrawViewPart','TechDraw::DrawViewSection','TechDraw::DrawViewDetail',
             'TechDraw::DrawViewDimension','TechDraw::DrawViewAnnotation')}),
        geometry_coverage_complete=bool(coverage) and all(row['edges_complete'] and row['vertices_complete'] for row in coverage.values()),
        rendered_text=rendered)
    if report['unsupported_view_types']:
        report['geometry_coverage_complete']=False
    return dict(document=document.Name,page=page.Name,**report)
