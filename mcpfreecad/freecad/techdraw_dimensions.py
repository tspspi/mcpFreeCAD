"""Projected geometry references, measured dimensions and drawing annotations."""
import hashlib
import json
import math
import re
import time

from . import techdraw as sheets

COMMANDS = frozenset({'get_techdraw_view_geometry', 'create_techdraw_dimension', 'create_techdraw_annotation',
    'update_techdraw_dimension', 'get_techdraw_dimension'})
DIMENSION_TYPES = ('Distance', 'DistanceX', 'DistanceY', 'Radius', 'Diameter', 'Angle', 'Angle3Pt')
STYLES = ('ISO Oriented', 'ISO Referencing', 'ASME Inlined', 'ASME Referencing')


def prepare(document, page, Gui):
    if Gui is None:
        raise RuntimeError('TechDraw geometry inspection and dimensions require the FreeCAD GUI')
    from PySide import QtCore
    deadline = time.monotonic() + 10.
    sheets._wait_for_projection(QtCore, deadline)
    document.recompute()
    page.ViewObject.show()
    page.requestPaint()
    sheets._wait_for_projection(QtCore, deadline)
    Gui.updateGui()


def _dimension(document, page, name):
    dim = document.getObject(name)
    if (dim is None or dim.TypeId != 'TechDraw::DrawViewDimension' or
            not any(item.Name == dim.Name for item in page.getAllViews())):
        raise ValueError('Choose an existing measured dimension on this drawing page')
    return dim


def _view(document, page, name):
    view = document.getObject(name)
    if (view is None or not view.isDerivedFrom('TechDraw::DrawViewPart') or
            not any(item.Name == view.Name for item in page.getAllViews())):
        raise ValueError('Choose a projected part view on this drawing page')
    return view


def _point(value):
    result = [float(value.x), float(value.y), float(value.z)]
    if not all(math.isfinite(x) for x in result):
        raise ValueError('Nonfinite projected geometry')
    return result


def reference(view, name):
    """Read the exact FreeCAD selection name, including its zero-based index."""
    if type(name) is not str or re.fullmatch(r'(Edge|Vertex)(0|[1-9][0-9]{0,6})', name) is None:
        raise ValueError('Use a projected Edge/Vertex name returned by geometry inspection')
    if name.startswith('Vertex'):
        shape = view.getVertexBySelection(name)
        if shape.isNull():raise ValueError('Unknown projected vertex')
        return dict(name=name, kind='vertex', point_mm=_point(shape.Point))
    edge = view.getEdgeBySelection(name)
    if edge.isNull():raise ValueError('Unknown projected edge')
    curve = edge.Curve
    result = dict(name=name, kind='edge', curve=type(curve).__name__, length_mm=float(edge.Length),
        orientation=str(edge.Orientation), vertices_mm=[_point(v.Point) for v in edge.Vertexes])
    if result['curve'] == 'Circle':
        result.update(center_mm=_point(curve.Center), axis=_point(curve.Axis), radius_mm=float(curve.Radius),
            parameters=[float(edge.FirstParameter),float(edge.LastParameter)])
    return result


def token(document, page, view, geometry):
    # These are projected-geometry references, not durable 3D topological names.
    # A token binds the inspected geometry and its current drawing transform.
    payload = dict(policy='techdraw-projected-reference-v1', document=document.Name,
        page=page.Name, view=view.Name, sources=[obj.Name for obj in view.Source],
        direction=list(view.Direction), x_direction=list(view.XDirection), rotation=float(view.Rotation),
        scale=float(view.Scale), x_mm=float(view.X), y_mm=float(view.Y), geometry=geometry)
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def geometry_page(document, page, view, kind, offset, limit):
    if kind not in ('edges','vertices') or type(offset) is not int or not 0 <= offset <= 1000000 or type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError('Geometry pages require edges/vertices, offset 0..1000000 and limit 1..256')
    prefix = 'Edge' if kind == 'edges' else 'Vertex'
    rows = []
    for i in range(offset,offset+limit+1):
        try:
            value = reference(view,prefix+str(i))
        except ValueError as exc:
            if str(exc) not in ('Wrong edge index', 'Wrong vertex index', 'Unknown projected edge', 'Unknown projected vertex'):
                raise
            break
        value['reference_token'] = token(document,page,view,value)
        rows.append(value)
    more = len(rows)>limit
    return dict(document=document.Name,page=page.Name,view=view.Name,kind=kind,
        coordinate_system='unscaled_projected_view_mm',scale=float(view.Scale),
        rotation_deg=float(view.Rotation),view_rotation_applied=True,view_state=list(view.State),offset=offset,
        view_preset=getattr(view,'MCPViewPreset',None),projection_kind=getattr(view,'MCPProjectionKind',None),
        returned=len(rows[:limit]),has_more=more,next_offset=offset+limit if more else None,
        geometry=rows[:limit])


def _references(document, page, view, values, kind):
    if type(values) is not list or not 1 <= len(values) <= 3:
        raise ValueError('Dimensions require 1..3 inspected geometry references')
    result=[]
    for value in values:
        if type(value) is not dict or set(value)!= {'name','reference_token'}:
            raise ValueError('Each dimension reference needs name and reference_token')
        actual=reference(view,value['name'])
        if value['reference_token'] != token(document,page,view,actual):
            raise ValueError('Projected geometry changed; inspect the view again before dimensioning')
        result.append(actual)
    names=[value['name'] for value in result]
    if len(set(names)) != len(names):raise ValueError('Dimension references must be distinct')
    vertices=all(value['kind']=='vertex' for value in result)
    lines=all(value.get('curve')=='Line' for value in result)
    edges=all(value['kind']=='edge' for value in result)
    mixed=len(result)==2 and {value['kind'] for value in result}=={'edge','vertex'}
    valid = ((kind in ('Distance','DistanceX','DistanceY') and
        (len(result)==1 and lines or len(result)==2 and (vertices or edges or mixed))) or
        (kind in ('Radius','Diameter') and len(result)==1 and result[0].get('curve')=='Circle') or
        (kind=='Angle' and len(result)==2 and lines) or
        (kind=='Angle3Pt' and len(result)==3 and vertices))
    if not valid:raise ValueError('Projected geometry does not match this dimension type')
    if len(result)==1 and kind in ('DistanceX','DistanceY'):
        points=result[0].get('vertices_mm') or []
        axis=0 if kind=='DistanceX' else 1
        if len(points)==2 and abs(points[0][axis]-points[1][axis])<1e-10:
            raise ValueError('Straight edge is perpendicular to the requested dimension axis')
    return names


def dimension_details(dimension):
    if hasattr(dimension,'isValid') and not dimension.isValid():
        raise ValueError('Dimension has invalid geometry references')
    value=float(dimension.getRawValue())
    if not math.isfinite(value):raise ValueError('Dimension has no finite measured value')
    result=dict(name=dimension.Name,type=str(dimension.Type),measure_type=str(dimension.MeasureType),
        font_size_mm=float(getattr(dimension.ViewObject,'Fontsize',3.5)),
        value=value,value_unit='deg' if str(dimension.Type).startswith('Angle') else 'mm2' if str(dimension.Type)=='Area' else 'mm',
        x_mm=float(dimension.X),y_mm=float(dimension.Y),position_frame='sheet_mm_relative_to_parent_view',
        references=[dict(view=view.Name,names=list(names)) for view,names in dimension.References2D],
        style=str(dimension.ViewObject.StandardAndStyle),format_spec=str(dimension.FormatSpec),
        arbitrary=bool(dimension.Arbitrary),upper_tolerance=float(dimension.OverTolerance),
        lower_tolerance=float(dimension.UnderTolerance),state=list(dimension.State),
        drawing_standard_conformance_certified=False)

    result.update(inverted=bool(getattr(dimension,'Inverted',False)),
        theoretical_exact=bool(getattr(dimension,'TheoreticalExact',False)),
        arbitrary_tolerances=bool(getattr(dimension,'ArbitraryTolerances',False)))
    refs=list(dimension.References2D)
    parent=refs[0][0] if refs else None
    scale=float(getattr(parent,'Scale',0.)) if parent is not None else 0.
    kind=str(dimension.Type)
    method=('getLinearPoints' if kind in ('Distance','DistanceX','DistanceY') else
        'getArcPoints' if kind in ('Radius','Diameter') else
        'getAnglePoints' if kind in ('Angle','Angle3Pt') else None)
    geometry=dict(coordinate_system='unscaled_projected_view_mm',available=False)
    if getattr(parent,'TypeId','TechDraw::DrawViewPart')!='TechDraw::DrawViewPart':
        geometry['reason']='derived_view_native_coordinate_frame_unverified'
    elif method and scale>0 and hasattr(dimension,method):
        try:
            # Native measurement points are scaled and Y-inverted. Selection
            # geometry is unscaled Y-up; normalize to that public frame.
            points=[_point(p) for p in getattr(dimension,method)()]
            geometry.update(available=True,points_mm=[[p[0]/scale,-p[1]/scale,p[2]/scale] for p in points])
            if hasattr(dimension,'getArrowPositions'):
                # GUI arrow positions are already unscaled in the page's
                # parent-view frame (unlike the internal measurement points).
                arrows=[_point(p) for p in dimension.getArrowPositions()]
                geometry['arrow_points_mm']=arrows
                geometry['arrow_positions_available']=len(arrows)==2 and any(abs(v)>1e-10 for p in arrows for v in p)
        except (ValueError,RuntimeError,AttributeError) as exc:
            geometry=dict(coordinate_system='unscaled_projected_view_mm',available=False,reason=type(exc).__name__)
    else:geometry['reason']='native_measurement_geometry_unavailable'
    result['measurement_geometry']=geometry
    return result


def _verify_dimension_geometry(view,names,details):
    from . import techdraw_measurement_checks
    rows=[reference(view,name) for name in names]
    errors=[]
    techdraw_measurement_checks.check_measurements([details],
        {view.Name:[r for r in rows if r['kind']=='edge']},
        {view.Name:[r for r in rows if r['kind']=='vertex']},errors.append)
    if any(f['severity']=='error' for f in errors):
        raise ValueError('FreeCAD measurement disagrees with the inspected reference frame; use an aligned Distance or repair the view/references')


def _font_size(value):
    if value is None:return None
    size=sheets._number(value,'dimension font size',positive=True)
    if size>100:raise ValueError('Dimension font size must not exceed 100 mm')
    return size


def execute(command, params, App, Gui, document):
    page=sheets._page(document,params.get('page_name'))
    if command=='create_techdraw_annotation':
        text=params.get('text')
        if type(text) is not str or not text.strip() or len(text)>8192:
            raise ValueError('Annotation text must contain 1..8192 characters')
        name=sheets._name(document,params.get('name','DrawingNote'))
        x=sheets._number(params.get('x_mm',20.),'annotation X')
        y=sheets._number(params.get('y_mm',30.),'annotation Y')
        size=sheets._number(params.get('font_size_mm',3.5),'annotation text size',positive=True)
        with sheets._edit(document,'Create TechDraw annotation'):
            note=document.addObject('TechDraw::DrawViewAnnotation',name)
            note.Text=text.splitlines();page.addView(note);note.X,note.Y=x,y
            note.TextSize=size
        return dict(document=document.Name,page=page.Name,annotation=note.Name,text=list(note.Text),
            x_mm=float(note.X),y_mm=float(note.Y),font_size_mm=float(note.TextSize))
    if command=='get_techdraw_dimension':
        dimension=_dimension(document,page,params.get('dimension_name'))
        prepare(document,page,Gui)
        return dict(document=document.Name,page=page.Name,dimension=dimension_details(dimension))
    if command=='update_techdraw_dimension':
        dimension=_dimension(document,page,params.get('dimension_name'))
        kind=str(dimension.Type)
        x_param,y_param=params.get('x_mm'),params.get('y_mm')
        x=None if x_param is None else sheets._number(x_param,'dimension X')
        y=None if y_param is None else sheets._number(y_param,'dimension Y')
        style=params.get('style')
        if style is not None and style not in STYLES:raise ValueError('Choose a FreeCAD ISO/ASME dimension style')
        precision=params.get('precision')
        if precision is not None and (type(precision) is not int or not 0<=precision<=6):
            raise ValueError('Dimension precision must be 0..6')
        font_size=_font_size(params.get('font_size_mm'))
        upper_param,lower_param=params.get('upper_tolerance'),params.get('lower_tolerance')
        if x is None and y is None and style is None and precision is None and upper_param is None and lower_param is None and font_size is None:
            raise ValueError('Provide at least one dimension field to update')
        upper=float(dimension.OverTolerance) if upper_param is None else sheets._number(upper_param,'upper tolerance')
        lower=float(dimension.UnderTolerance) if lower_param is None else sheets._number(lower_param,'lower tolerance')
        if lower>upper:raise ValueError('Lower tolerance must not exceed upper tolerance')
        with sheets._edit(document,'Update TechDraw dimension'):
            if x is not None:dimension.X=x
            if y is not None:dimension.Y=y
            if style is not None:dimension.ViewObject.StandardAndStyle=style
            if font_size is not None:dimension.ViewObject.Fontsize=font_size
            if precision is not None:
                number='%.'+str(precision)+'f'
                # FreeCAD inserts the angular unit itself, even with units hidden.
                dimension.FormatSpec={'Diameter':'⌀','Radius':'R'}.get(kind,'')+number
            dimension.OverTolerance=upper;dimension.UnderTolerance=lower
            document.recompute()
            if not dimension.isValid():raise ValueError('FreeCAD rejected the updated dimension')
            details=dimension_details(dimension)
        return dict(document=document.Name,page=page.Name,dimension=details)
    view=_view(document,page,params.get('view_name'))
    prepare(document,page,Gui)
    if command=='get_techdraw_view_geometry':
        return geometry_page(document,page,view,params.get('kind','edges'),params.get('offset',0),params.get('limit',64))
    if command!='create_techdraw_dimension':raise ValueError('Unknown TechDraw dimension command')
    kind=params.get('dimension_type','Distance')
    if kind not in DIMENSION_TYPES:raise ValueError('Unsupported dimension type')
    names=_references(document,page,view,params.get('references'),kind)
    name=sheets._name(document,params.get('name','DrawingDimension'))
    style=params.get('style','ISO Oriented')
    if style not in STYLES:raise ValueError('Choose a FreeCAD ISO/ASME dimension style')
    precision=params.get('precision',2)
    if type(precision) is not int or not 0 <= precision <= 6:raise ValueError('Dimension precision must be 0..6')
    font_size=_font_size(params.get('font_size_mm'))
    x=sheets._number(params.get('x_mm',0.),'dimension X')
    y=sheets._number(params.get('y_mm',25.),'dimension Y')
    upper=sheets._number(params.get('upper_tolerance',0.),'upper tolerance')
    lower=sheets._number(params.get('lower_tolerance',0.),'lower tolerance')
    if lower>upper:raise ValueError('Lower tolerance must not exceed upper tolerance')
    with sheets._edit(document,'Create measured TechDraw dimension'):
        dimension=document.addObject('TechDraw::DrawViewDimension',name)
        dimension.Type=kind;dimension.MeasureType='Projected'
        dimension.References2D=[(view,ref) for ref in names]
        dimension.Arbitrary=False;dimension.ArbitraryTolerances=False
        number='%.'+str(precision)+'f'
        # A numeric precision override must preserve dimension semantics in the
        # printed sheet, including diameter/radius signs and angular units.
        dimension.FormatSpec={'Diameter':'\u2300','Radius':'R'}.get(kind,'')+number
        dimension.EqualTolerance=False
        dimension.OverTolerance=upper;dimension.UnderTolerance=lower
        dimension.ViewObject.StandardAndStyle=style
        if font_size is not None:dimension.ViewObject.Fontsize=font_size
        page.addView(dimension);dimension.X,dimension.Y=x,y
        document.recompute()
        if not dimension.isValid():raise ValueError('FreeCAD rejected the projected dimension')
        details=dimension_details(dimension)
        _verify_dimension_geometry(view,names,details)
    return dict(document=document.Name,page=page.Name,dimension=details)
