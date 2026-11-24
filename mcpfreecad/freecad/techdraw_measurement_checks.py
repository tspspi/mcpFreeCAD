"""Bounded numeric/symbol checks for geometry-linked drawing dimensions."""
import math

LINEAR = ('Distance','DistanceX','DistanceY')
SUPPORTED = LINEAR+('Radius','Diameter','Angle','Angle3Pt')


def _angle(a,b,c):
    u=[a[i]-b[i] for i in range(2)];v=[c[i]-b[i] for i in range(2)]
    norm=math.hypot(*u)*math.hypot(*v)
    if norm<=1e-12:raise ValueError('Degenerate angular references')
    return math.degrees(math.acos(max(-1.,min(1.,sum(x*y for x,y in zip(u,v))/norm))))


def _point_segment(point,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    square=dx*dx+dy*dy
    if square<=1e-20:raise ValueError('Degenerate line')
    t=max(0.,min(1.,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/square))
    return [a[0]+t*dx,a[1]+t*dy,0.]


def _line_pair(rows):
    if len(rows)!=2:return []
    points=[r for r in rows if 'point_mm' in r]
    lines=[r for r in rows if r.get('curve')=='Line' and len(r.get('vertices_mm') or [])==2]
    if len(points)==1 and len(lines)==1:
        p=points[0]['point_mm']
        return [p,_point_segment(p,*lines[0]['vertices_mm'])]
    if len(lines)!=2:return []
    a,b=lines[0]['vertices_mm'];c,d=lines[1]['vertices_mm']
    u=(b[0]-a[0],b[1]-a[1]);v=(d[0]-c[0],d[1]-c[1])
    det=u[0]*v[1]-u[1]*v[0]
    if abs(det)>1e-12:
        w=(c[0]-a[0],c[1]-a[1])
        t=(w[0]*v[1]-w[1]*v[0])/det;q=(w[0]*u[1]-w[1]*u[0])/det
        if 0<=t<=1 and 0<=q<=1:
            p=[a[0]+t*u[0],a[1]+t*u[1],0.]
            return [p,p]
    candidates=[[p,_point_segment(p,c,d)] for p in (a,b)]+[[_point_segment(p,a,b),p] for p in (c,d)]
    return min(candidates,key=lambda pair:math.dist(*pair))


def check_measurements(dimensions,edges,vertices,emit):
    edge_index={(view,row['name']):row for view,rows in edges.items() for row in rows}
    vertex_index={(view,row['name']):row for view,rows in vertices.items() for row in rows}
    checked=[];skipped=[];sources={}
    for dim in dimensions:
        name=dim['name'];kind=dim.get('type')
        if kind not in SUPPORTED:
            skipped.append(dict(dimension=name,reason='unsupported_dimension_type'));continue
        if dim.get('measure_type','Projected')!='Projected':
            skipped.append(dict(dimension=name,reason='true_3d_measurement_requires_3d_geometry'));continue
        if dim.get('arbitrary'):
            emit(dict(check='arbitrary_dimension_value',severity='error',dimension=name,
                message='An arbitrary displayed value overrides the geometric measurement. Restore a measured dimension.'))
            skipped.append(dict(dimension=name,reason='arbitrary_value'));continue
        if dim.get('arbitrary_tolerances'):
            emit(dict(check='arbitrary_tolerance_text',severity='warning',dimension=name,
                message='Tolerance text is overridden; numeric tolerance properties do not establish the displayed limits.'))
        if dim.get('theoretical_exact') and (dim.get('upper_tolerance') or dim.get('lower_tolerance')):
            emit(dict(check='basic_dimension_with_limits',severity='error',dimension=name,
                message='A theoretically exact/basic dimension carries nonzero individual limits. Reconcile its specification.'))
        refs=dim.get('references') or []
        rows=[]
        for ref in refs:
            for sub in ref.get('names') or []:
                row=vertex_index.get((ref['view'],sub)) if sub.startswith('Vertex') else edge_index.get((ref['view'],sub))
                if row is not None:rows.append(row)
        points=[];expected=None;alternate=None;source='inspected_references'
        native=dim.get('measurement_geometry') or {}
        try:
            if kind in ('Radius','Diameter') and len(rows)==1 and rows[0].get('curve')=='Circle':
                expected=rows[0]['radius_mm']*(2 if kind=='Diameter' else 1)
            elif kind in LINEAR:
                if len(rows)==2 and all('point_mm' in row for row in rows):points=[row['point_mm'] for row in rows]
                elif len(rows)==1 and rows[0].get('curve')=='Line':points=rows[0].get('vertices_mm') or []
                else:points=_line_pair(rows)
                if len(points)!=2 and native.get('available'):
                    points=native.get('points_mm') or [];source='native_closest_points'
                if len(points)==2:
                    expected=(math.dist(points[0],points[1]) if kind=='Distance' else
                        abs(points[0][0 if kind=='DistanceX' else 1]-points[1][0 if kind=='DistanceX' else 1]))
            elif kind=='Angle3Pt' and len(rows)==3 and all('point_mm' in row for row in rows):
                expected=_angle(*[row['point_mm'] for row in rows])
            elif kind=='Angle' and len(rows)==2 and all(row.get('curve')=='Line' for row in rows):
                a,b=rows[0]['vertices_mm'];c,d=rows[1]['vertices_mm']
                expected=_angle([a[i]-b[i] for i in range(2)],[0.,0.],[c[i]-d[i] for i in range(2)])
                alternate=180.-expected  # Native placement can choose either supplementary angle.
            if expected is None:
                skipped.append(dict(dimension=name,reason='reference_geometry_unavailable'));continue
            if dim.get('inverted'):
                expected=360.-expected if kind.startswith('Angle') else -expected
                if alternate is not None:alternate=360.-alternate
            value=dim.get('value')
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Invalid measurement')
            if not math.isclose(value,expected,abs_tol=1e-6,rel_tol=1e-8) and not (
                    alternate is not None and math.isclose(value,alternate,abs_tol=1e-6,rel_tol=1e-8)):
                emit(dict(check='dimension_geometry_value_mismatch',severity='error',dimension=name,
                    value=value,expected_value=expected,expected_unit='deg' if kind.startswith('Angle') else 'mm',
                    message='Raw measurement disagrees with its inspected geometry. Refresh/repair references and recheck.'))
            checked.append(name);sources[name]=source
        except (ValueError,TypeError,KeyError,IndexError,OverflowError):
            skipped.append(dict(dimension=name,reason='unresolved_or_degenerate_geometry'))
            emit(dict(check='dimension_geometry_unresolved',severity='warning',dimension=name,
                message='Referenced geometry could not establish this measurement. Inspect and repair it before acceptance.'))
        spec=dim.get('format_spec')
        # Native DimensionFormatter always appends the angular unit itself.
        required={'Radius':'R','Diameter':'⌀'}.get(kind)
        if spec is not None and required and required not in spec and not (kind=='Diameter' and 'Ø' in spec):
            emit(dict(check='dimension_semantic_symbol_missing',severity='warning',dimension=name,
                message=f'{kind} format omits its {required} symbol. Inspect the exported label and restore its meaning.'))
        if kind.startswith('Angle') and spec is not None and '°' in spec:
            emit(dict(check='duplicate_angular_unit',severity='warning',dimension=name,
                message='FreeCAD appends the angular unit automatically. Remove the literal degree sign from FormatSpec and inspect the export.'))
    return dict(dimensions_checked=checked,dimensions_skipped=skipped,calculation_sources=sources,
        supported_types=list(SUPPORTED),coverage_complete=not skipped,feature_completeness_certified=False)
