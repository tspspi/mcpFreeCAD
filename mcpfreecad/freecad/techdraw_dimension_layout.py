"""Bounded dimension-label/extension-line estimates in absolute sheet mm.

Only axis-aligned linear dimensions with resolved 2D references are covered.
Font boxes and extension overshoot are estimates, not glyph measurements.
"""
import math


def _intersects(box, a, b):
    """Closed rectangle versus an axis-aligned segment, including endpoints."""
    if abs(a[0]-b[0]) < 1e-8:
        return box[0] <= a[0] <= box[2] and max(min(a[1],b[1]),box[1]) <= min(max(a[1],b[1]),box[3])
    if abs(a[1]-b[1]) < 1e-8:
        return box[1] <= a[1] <= box[3] and max(min(a[0],b[0]),box[0]) <= min(max(a[0],b[0]),box[2])
    return False


def check_layout(page, dimensions, edges, vertices, emit):
    views={v['name']:v for v in page['views']}
    layouts=[]; skipped=[]
    for dim in dimensions:
        if dim.get('arbitrary') or dim.get('type') not in ('Distance','DistanceX','DistanceY'):
            continue
        refs=dim.get('references') or []
        if len(refs)!=1 or refs[0]['view'] not in views:
            skipped.append(dim['name']);continue
        view=views[refs[0]['view']];points=[]
        for name in refs[0].get('names') or []:
            if name.startswith('Vertex'):
                row=next((r for r in vertices.get(view['name'],[]) if r['name']==name),None)
                if row:points.append(row['point_mm'])
            else:
                row=next((r for r in edges.get(view['name'],[]) if r['name']==name),None)
                if row:points.extend(row.get('vertices_mm') or [])
        if len(points)!=2 and (dim.get('measurement_geometry') or {}).get('available'):
            points=dim['measurement_geometry'].get('points_mm') or []
        if len(points)!=2:
            skipped.append(dim['name']);continue
        axis=0 if dim['type']=='DistanceX' else 1 if dim['type']=='DistanceY' else (
            0 if abs(points[0][1]-points[1][1])<1e-8 else 1 if abs(points[0][0]-points[1][0])<1e-8 else None)
        if axis is None or dim.get('style','ISO Oriented')!='ISO Oriented':
            skipped.append(dim['name']);continue
        size=dim.get('font_size_mm',3.5)
        if type(size) not in (int,float) or not math.isfinite(size) or not 0<size<=100:
            skipped.append(dim['name']);continue
        value=dim.get('value')
        if type(value) not in (int,float) or not math.isfinite(value):
            skipped.append(dim['name']);continue
        try:text=dim.get('format_spec','%.2f') % value
        except (ValueError,TypeError,OverflowError):text=f'{value:g}'
        if len(text)>128:
            skipped.append(dim['name']);continue
        width=max(size*.5,len(text)*size*.55)
        if dim.get('upper_tolerance') or dim.get('lower_tolerance'):width+=size*3
        x=view['x_mm']+dim['x_mm'];y=view['y_mm']+dim['y_mm']
        # ISO Oriented text sits above the dimension line, rotated for Y.
        box=(x-width/2,y+size*.2,x+width/2,y+size*1.2) if axis==0 else (
            x-size*1.2,y-width/2,x-size*.2,y+width/2)
        projected=[(view['x_mm']+p[0]*view['scale'],view['y_mm']+p[1]*view['scale']) for p in points]
        lines=[]
        for p in projected:
            end=(p[0],y+math.copysign(size*.5,y-p[1])) if axis==0 else (x+math.copysign(size*.5,x-p[0]),p[1])
            lines.append((p,end))
        dimension_line=((projected[0][0],y),(projected[1][0],y)) if axis==0 else ((x,projected[0][1]),(x,projected[1][1]))
        layouts.append(dict(name=dim['name'],box=box,helpers=lines,line=dimension_line))
    for label in layouts:
        for owner in layouts:
            if any(_intersects(label['box'],a,b) for a,b in owner['helpers']):
                emit(dict(check='dimension_text_extension_line',severity='warning',dimension=label['name'],
                    extension_owner=owner['name'],bounds_estimated=True,
                    message=f"{label['name']}'s estimated label box intersects an extension line of {owner['name']}. "
                        'Inspect the rendered text and helper line; centre or move the label/line if they cross.'))
    template=page.get('template') or {};profile=page.get('drawing_profile') or {}
    if profile.get('preset') and template.get('width_mm') is not None:
        width=template['width_mm'];block=(width-190.,10.,width-10.,58.)
        for layout in layouts:
            if any(_intersects(block,a,b) for a,b in [layout['line']]+layout['helpers']):
                emit(dict(check='dimension_line_title_block',severity='warning',dimension=layout['name'],bounds_estimated=True,
                    message=f"{layout['name']}'s estimated dimension/extension lines reach the assumed preset title block. "
                        'Inspect the actual template and move the dimension or view if needed.'))
    return dict(axis_aligned_dimensions_checked=len(layouts),dimensions_skipped=skipped,
        text_bounds_estimated=True,extension_overshoot_estimated=True,
        title_block_bounds='assumed_preset' if profile.get('preset') else 'unavailable')
