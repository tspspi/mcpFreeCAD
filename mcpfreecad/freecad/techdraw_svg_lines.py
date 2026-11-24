"""Bounded straight SVG stroke extraction for estimated text/line review."""
import math
import re

_NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
_TOKEN=re.compile('[MmLlHhVvZz]|'+_NUMBER)
MAX_SEGMENTS=4096
MAX_TESTS=65536


def segments(node,style):
    name=node.tag.rsplit('}',1)[-1]
    if style.get('stroke','none')=='none':return [],None
    try:
        if name=='line':
            return [((float(node.get('x1','0')),float(node.get('y1','0'))),(float(node.get('x2','0')),float(node.get('y2','0'))))],None
        if name in ('polyline','polygon'):
            raw=node.get('points','')
            if re.sub(_NUMBER,'',raw).strip(' ,\t\r\n'):return [],'unsupported_points'
            values=[float(s) for s in re.findall(_NUMBER,raw)]
            if len(values)%2 or len(values)>MAX_SEGMENTS*2:return [],'line_segment_limit'
            points=list(zip(values[::2],values[1::2]));result=list(zip(points,points[1:]))
            if name=='polygon' and len(points)>1:result.append((points[-1],points[0]))
            return result,None
        if name in ('circle','ellipse'):return [],'curved_or_unsupported_path'
        if name=='rect':
            if node.get('rx') or node.get('ry'):return [],'curved_or_unsupported_path'
            x,y=float(node.get('x','0')),float(node.get('y','0'))
            w,h=float(node.get('width','0')),float(node.get('height','0'))
            if w<0 or h<0:raise ValueError('Negative rectangle size')
            points=[(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]
            return list(zip(points,points[1:])),None
        if name=='path':
            raw=node.get('d','')
            if re.sub(_TOKEN,'',raw).strip(' ,\t\r\n'):return [],'curved_or_unsupported_path'
            tokens=_TOKEN.findall(raw)
            if len(tokens)>MAX_SEGMENTS*8:return [],'line_segment_limit'
            if any(t in ('z','Z') for t in tokens) and style.get('fill','black')!='none':return [],None  # Filled arrowheads are not helper lines.
            i=0;command=None;p=(0.,0.);start=p;result=[]
            while i<len(tokens):
                if tokens[i].isalpha():command=tokens[i];i+=1
                if command is None:raise ValueError('Missing path command')
                if command in ('z','Z'):
                    result.append((p,start));p=start;command=None;continue
                count=1 if command.upper() in ('H','V') else 2
                values=[float(v) for v in tokens[i:i+count]]
                if len(values)!=count:raise ValueError('Incomplete path')
                i+=count;relative=command.islower();op=command.upper()
                if op in ('M','L'):q=(values[0]+(p[0] if relative else 0.),values[1]+(p[1] if relative else 0.))
                elif op=='H':q=(values[0]+(p[0] if relative else 0.),p[1])
                else:q=(p[0],values[0]+(p[1] if relative else 0.))
                if op=='M':start=q;command='l' if relative else 'L'
                else:result.append((p,q))
                p=q
            return result,None
    except (ValueError,TypeError,OverflowError):return [],'invalid_line_geometry'
    return [],None


def intersects(box,a,b):
    """Rectangle clipping supports arbitrary segment direction and zero length."""
    low,high=0.,1.
    dx,dy=b[0]-a[0],b[1]-a[1]
    for p,q in ((-dx,a[0]-box[0]),(dx,box[2]-a[0]),(-dy,a[1]-box[1]),(dy,box[3]-a[1])):
        if abs(p)<1e-15:
            if q<0:return False
        else:
            t=q/p
            if p<0:low=max(low,t)
            else:high=min(high,t)
            if low>high:return False
    return True


def intersects_quad(points,a,b):
    origin=points[0]
    u=(points[1][0]-origin[0],points[1][1]-origin[1])
    v=(points[2][0]-origin[0],points[2][1]-origin[1])
    determinant=u[0]*v[1]-u[1]*v[0]
    if abs(determinant)<1e-12:return False
    def local(p):
        x,y=p[0]-origin[0],p[1]-origin[1]
        return ((x*v[1]-y*v[0])/determinant,(u[0]*y-u[1]*x)/determinant)
    return intersects((0.,0.,1.,1.),local(a),local(b))


def check(boxes,strokes,gaps,emit,quads=None):
    tests=0;found=set()
    for box_index,(text,box) in enumerate(boxes):
        for index,(a,b) in enumerate(strokes):
            if tests>=MAX_TESTS:
                gaps.add('text_line_test_limit');break
            tests+=1
            hit=intersects_quad(quads[box_index],a,b) if quads is not None else intersects(box,a,b)
            if hit and (text,box) not in found:
                found.add((text,box))
                emit(dict(check='svg_text_line_overlap',severity='warning',text=text,stroke_index=index,
                    bounds_estimated=True,message='An estimated text box intersects a rendered straight stroke. '
                    'Inspect the label and line in the MCP image; glyph bounds are estimates and the crossing can be intentional.'))
        if tests>=MAX_TESTS:break
    return dict(segments_checked=len(strokes),text_line_tests=tests,coverage_complete=not gaps,
        coverage_gaps=sorted(gaps),curved_strokes_checked=False,glyph_metrics_measured=False)
