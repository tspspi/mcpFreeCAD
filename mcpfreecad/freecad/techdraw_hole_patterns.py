"""Measured circular patterns and their drawing-only pitch-circle linework.

The circle is a derived snapshot. Mandatory page checks detect stale references
or changed linework; no automatic parametric recompute is claimed.
"""
import json
import math
import re

MAX_HOLES = 64
MAX_PATTERNS = 16
MATCH_MM = 1e-5  # Numerical matching, never a manufacturing tolerance.


def analyze(rows):
    if not 3<=len(rows)<=MAX_HOLES:
        raise ValueError('Choose 3..64 circular hole edges from one view')
    points=[];radii=[]
    for row in rows:
        if row.get('curve')!='Circle':raise ValueError('Hole pattern references must be circular edges')
        point=row.get('center_mm');radius=row.get('radius_mm')
        if (not point or len(point)!=3 or not all(math.isfinite(v) and abs(v)<=1e7 for v in point)
                or not isinstance(radius,(int,float)) or not math.isfinite(radius) or not 0<radius<=1e7):
            raise ValueError('Invalid hole geometry')
        if any(math.dist(point,previous)<MATCH_MM for previous in points):
            raise ValueError('Repeated hole centre; select one edge per hole, not split arcs or concentric steps')
        points.append(point);radii.append(radius)
    if max(radii)-min(radii)>MATCH_MM:
        raise ValueError('Choose one pattern with identical hole sizes')
    a,b=points[:2];ux,uy=b[0]-a[0],b[1]-a[1]
    for c in points[2:]:
        vx,vy=c[0]-a[0],c[1]-a[1];det=2*(ux*vy-uy*vx)
        if abs(det)>1e-10:break
    else:raise ValueError('Hole centres are collinear; pitch circle is undefined')
    usq,vsq=ux*ux+uy*uy,vx*vx+vy*vy
    center=[a[0]+(usq*vy-vsq*uy)/det,a[1]+(ux*vsq-vx*usq)/det,0.]
    distances=[math.dist(center,p) for p in points];radius=sum(distances)/len(points)
    if radius<=MATCH_MM or max(distances)-min(distances)>MATCH_MM:
        raise ValueError('Hole centres do not lie on one pitch circle')
    angles=sorted(math.degrees(math.atan2(p[1]-center[1],p[0]-center[0]))%360. for p in points)
    gaps=[(angles[(i+1)%len(angles)]-angle)%360. for i,angle in enumerate(angles)]
    spacing=360./len(points)
    equal=max(abs(gap-spacing)*math.pi/180.*radius for gap in gaps)<=MATCH_MM
    return dict(center_mm=center,pitch_radius_mm=radius,pitch_diameter_mm=2*radius,
        hole_diameter_mm=2*sum(radii)/len(radii),hole_count=len(points),angles_deg=angles,
        equally_spaced=equal,spacing_deg=spacing if equal else None,
        coordinate_system='unscaled_projected_view_mm',associativity='validated_snapshot',
        numerical_matching_mm=MATCH_MM,conformance_certified=False)


def records(view):
    raw=getattr(view,'MCPHolePatterns','')
    if not raw:return []
    if not isinstance(raw,str) or len(raw)>131072:raise ValueError('Hole pattern metadata exceeds limit')
    values=json.loads(raw)
    if type(values) is not list or len(values)>MAX_PATTERNS:raise ValueError('Invalid hole pattern metadata')
    if any(type(record) is not dict for record in values):raise ValueError('Invalid hole pattern record')
    return values


def execute(params,App,Gui,document):
    from . import techdraw as sheets,techdraw_dimensions as dims
    page=sheets._page(document,params.get('page_name'))
    view=sheets._base_part_view(document,page,params.get('view_name'))
    dims.prepare(document,page,Gui)
    name=params.get('name','HolePattern')
    if type(name) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}',name):
        raise ValueError('Choose a pattern name of at most 80 ASCII letters, digits or underscores')
    existing=records(view)
    if len(existing)>=MAX_PATTERNS or any(record['name']==name for record in existing):
        raise ValueError('Pattern already exists or this view reached the pattern limit')
    refs=params.get('references')
    if type(refs) is not list or not 3<=len(refs)<=MAX_HOLES:
        raise ValueError('Choose 3..64 inspected hole edges')
    rows=[]
    for ref in refs:
        if type(ref) is not dict or set(ref)!={'name','reference_token'}:
            raise ValueError('Each hole reference needs name and reference_token')
        row=dims.reference(view,ref['name'])
        if ref['reference_token']!=dims.token(document,page,view,row):
            raise ValueError('Projected geometry changed; inspect the hole edges again')
        try:view.getCosmeticEdgeBySelection(ref['name'])
        except ValueError:pass
        else:raise ValueError('Choose source hole geometry, not cosmetic circles')
        rows.append(row)
    pattern=analyze(rows)
    if params.get('require_equal_spacing',True) is not True and params.get('require_equal_spacing') is not False:
        raise ValueError('require_equal_spacing must be boolean')
    if params.get('require_equal_spacing',True) and not pattern['equally_spaced']:
        raise ValueError('Hole centres are not equally spaced; inspect the pattern or explicitly allow unequal spacing')
    with sheets._edit(document,'Create inspected hole pitch circle'):
        # Qt DashDotLine = 4: distinguish centre linework from the visible part.
        tag=view.makeCosmeticCircle(App.Vector(*pattern['center_mm']),pattern['pitch_radius_mm'],4,.18,(0.,0.,0.))
        if 'MCPHolePatterns' not in view.PropertiesList:
            view.addProperty('App::PropertyString','MCPHolePatterns','MCP Drawing')
        record=dict(name=name,cosmetic_tag=tag,references=[row['name'] for row in rows],**pattern)
        view.MCPHolePatterns=json.dumps(existing+[record],allow_nan=False)
        view.ViewObject.ArcCenterMarks=True
    return dict(document=document.Name,page=page.Name,view=view.Name,pattern=record,
        dimension_required=True,location_and_orientation_required=True)


def check(view,edges,emit):
    """Recheck source features and pitch linework; never certify completeness."""
    from . import techdraw_dimensions as dims
    checked=[]
    for record in records(view):
        name=record.get('name','unknown')
        try:
            refs=record['references']
            if type(refs) is not list or not 3<=len(refs)<=MAX_HOLES:raise ValueError('Invalid pattern references')
            pattern=analyze([dims.reference(view,ref) for ref in refs])
            angles=record['angles_deg']
            if type(angles) is not list or len(angles)!=pattern['hole_count']:
                raise ValueError('Invalid angle snapshot')
            if (math.dist(pattern['center_mm'],record['center_mm'])>MATCH_MM or
                    abs(pattern['pitch_radius_mm']-record['pitch_radius_mm'])>MATCH_MM or
                    abs(pattern['hole_diameter_mm']-record['hole_diameter_mm'])>MATCH_MM or
                    pattern['hole_count']!=record['hole_count'] or
                    any(min(abs((a-b+180.)%360.-180.) for b in angles)>1e-5 for a in pattern['angles_deg'])):
                raise ValueError('Source pattern changed')
            # FreeCAD 1.0 regenerates cosmetic UUIDs when reopening FCStd.
            # With an absent UUID, require one unique matching cosmetic circle;
            # a coincident source-part circle must never satisfy this check.
            try:view.getCosmeticEdge(record['cosmetic_tag']);known_tag=True
            except ValueError:known_tag=False
            matches=[]
            for edge in edges:
                if (edge.get('curve')!='Circle' or math.dist(edge['center_mm'],pattern['center_mm'])>MATCH_MM or
                        abs(edge['radius_mm']-pattern['pitch_radius_mm'])>MATCH_MM):continue
                try:cosmetic=view.getCosmeticEdgeBySelection(edge['name'])
                except ValueError:continue
                if (not known_tag or cosmetic.Tag==record['cosmetic_tag']) and cosmetic.Format.get('style')==4 and cosmetic.Format.get('visible'):
                    matches.append(edge)
            if len(matches)!=1:raise ValueError('Pitch circle not resolved in inspected geometry')
            circle=matches[0]
            if (circle.get('curve')!='Circle' or math.dist(circle['center_mm'],pattern['center_mm'])>MATCH_MM or
                    abs(circle['radius_mm']-pattern['pitch_radius_mm'])>MATCH_MM):
                raise ValueError('Pitch circle linework disagrees with source hole centres')
            checked.append(dict(name=name,edge=circle['name'],
                cosmetic_reference_resolution='native_tag' if known_tag else 'unique_geometry_after_restore',**pattern))
        except (KeyError,TypeError,ValueError,AttributeError,RuntimeError):
            emit(dict(check='hole_pattern_stale_or_unresolved',severity='error',view=view.Name,pattern=name,
                message='Hole references, pattern snapshot or pitch-circle linework changed/could not be verified. Rebuild the pattern and its dimensions from inspected geometry.'))
    return checked
