"""Bounded SVG text-box estimates; visual review remains required.

Transforms and inherited font attributes locate labels, but character-count
widths do not measure glyphs. Findings are review warnings, never proof that
text is illegible or that an unflagged drawing is clear.
"""
import math
import re
import xml.etree.ElementTree as ET

SVG_NS = 'http://www.w3.org/2000/svg'
MAX_BYTES = 4 * 1024 * 1024
MAX_NODES = 20000
MAX_LABELS = 512
MAX_PAIR_TESTS = 65536
MAX_FINDINGS = 256
AVERAGE_CHAR_WIDTH_FACTOR = 0.55
TEXT_HEIGHT_FACTOR = 0.72
IDENTITY = (1., 0., 0., 1., 0., 0.)
_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
_TRANSFORM = re.compile(r'([A-Za-z]+)\s*\(([^()]*)\)')
_LENGTH = re.compile(r'^\s*(' + _NUMBER + r')\s*(px|mm|cm|in|pt|pc)?\s*$')


def _multiply(left, right):
    a,b,c,d,e,f = left; g,h,i,j,k,l = right
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)


def _parse_matrix(transform):
    """Compose an SVG transform list in SVG order; reject unsupported syntax."""
    result = IDENTITY
    if not transform:
        return result
    end = 0
    for match in _TRANSFORM.finditer(transform):
        if transform[end:match.start()].strip(' ,\t\r\n'):
            raise ValueError('Unsupported SVG transform')
        raw = match.group(2)
        values = [float(number) for number in re.findall(_NUMBER, raw)]
        if re.sub(_NUMBER, '', raw).strip(' ,\t\r\n') or not all(map(math.isfinite, values)):
            raise ValueError('Invalid SVG transform coordinates')
        name = match.group(1)
        if name == 'matrix' and len(values) == 6:
            matrix = tuple(values)
        elif name == 'translate' and len(values) in (1,2):
            matrix = (1.,0.,0.,1.,values[0],values[1] if len(values)==2 else 0.)
        elif name == 'scale' and len(values) in (1,2):
            matrix = (values[0],0.,0.,values[-1],0.,0.)
        elif name == 'rotate' and len(values) in (1,3):
            angle = math.radians(values[0]); c,s = math.cos(angle),math.sin(angle)
            matrix = (c,s,-s,c,0.,0.)
            if len(values)==3:
                x,y = values[1:]
                matrix = _multiply(_multiply((1,0,0,1,x,y),matrix),(1,0,0,1,-x,-y))
        elif name in ('skewX','skewY') and len(values)==1:
            tangent = math.tan(math.radians(values[0]))
            matrix = (1,0,tangent,1,0,0) if name=='skewX' else (1,tangent,0,1,0,0)
        else:
            raise ValueError('Unsupported SVG transform')
        result = _multiply(result,matrix); end = match.end()
    if transform[end:].strip(' ,\t\r\n') or not all(map(math.isfinite,result)):
        raise ValueError('Invalid SVG transform')
    return result


def _apply_matrix(matrix, x, y):
    a,b,c,d,e,f = matrix
    return a*x+c*y+e, b*x+d*y+f


def _boxes_overlap(a, b):
    return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]


def _quads_overlap(a,b):
    for points in (a,b):
        for edge in ((0,1),(0,2)):
            dx=points[edge[1]][0]-points[edge[0]][0];dy=points[edge[1]][1]-points[edge[0]][1]
            if abs(dx)+abs(dy)<1e-12:return False
            axis=(-dy,dx)
            pa=[p[0]*axis[0]+p[1]*axis[1] for p in a]
            pb=[p[0]*axis[0]+p[1]*axis[1] for p in b]
            if max(pa)<=min(pb) or max(pb)<=min(pa):return False
    return True


def _length(value):
    match = _LENGTH.fullmatch(str(value))
    if match is None:
        raise ValueError('Unsupported SVG length')
    number = float(match.group(1))
    if not math.isfinite(number):
        raise ValueError('Nonfinite SVG length')
    return number * {None:1.,'px':1.,'mm':96/25.4,'cm':96/2.54,'in':96.,'pt':96/72,'pc':16.}[match.group(2)]


def analyze_svg(svg_bytes, font_metrics=None):
    """Return estimated overlaps and explicit coverage gaps, in SVG coordinates.

    A shared viewport transform cannot change overlap existence, so physical
    millimetres and a fixed viewBox conversion are unnecessary here. Nested
    transforms within the drawing are composed before comparing boxes.
    """
    if isinstance(svg_bytes,str):
        svg_bytes = svg_bytes.encode('utf8')
    if type(svg_bytes) is not bytes or len(svg_bytes)>MAX_BYTES or b'<!ENTITY' in svg_bytes.upper():
        raise ValueError('SVG exceeds the text-check input contract')
    root = ET.fromstring(svg_bytes)
    if root.tag != '{'+SVG_NS+'}svg':
        raise ValueError('Expected an SVG document')
    from . import techdraw_svg_lines
    boxes = []; quads = []; gaps = set(); nodes = 0
    strokes = []; line_gaps = set(); measured_labels = 0
    stack = [(root,IDENTITY,{},0)]
    while stack:
        node,parent,style,depth = stack.pop(); nodes += 1
        if nodes>MAX_NODES or depth>64:
            gaps.add('svg_node_or_depth_limit'); break
        name = node.tag.rsplit('}',1)[-1]
        if name in ('defs','metadata','title','desc','clipPath','mask','symbol'):
            continue
        if name in ('style','use'):
            gaps.add('css_or_use_content_not_resolved'); continue
        current = dict(style)
        current.update({key:node.get(key) for key in ('font-size','font-family','font-style','font-weight','text-anchor','display','visibility','stroke','fill') if node.get(key) is not None})
        current.update(part.strip().split(':',1) for part in node.get('style','').split(';') if ':' in part)
        current = {key.strip():value.strip() for key,value in current.items()}
        if current.get('display')=='none' or current.get('visibility') in ('hidden','collapse'):
            continue
        try:
            matrix = _multiply(parent,_parse_matrix(node.get('transform')))
        except ValueError:
            gaps.add('unsupported_transform'); continue
        if name=='svg' and node is not root:
            gaps.add('nested_svg_viewport_not_resolved'); continue
        raw_segments,line_gap = techdraw_svg_lines.segments(node,current)
        if line_gap:line_gaps.add(line_gap)
        for first,second in raw_segments:
            if len(strokes)>=techdraw_svg_lines.MAX_SEGMENTS:
                line_gaps.add('line_segment_limit');break
            a,b=_apply_matrix(matrix,*first),_apply_matrix(matrix,*second)
            if not all(math.isfinite(v) for point in (a,b) for v in point):
                line_gaps.add('nonfinite_line_geometry');continue
            if a!=b:strokes.append((a,b))
        if name=='text':
            text = ''.join(node.itertext()).strip()
            if not text:
                continue
            if len(text)>8192:
                gaps.add('text_character_limit'); continue
            if len(boxes)>=MAX_LABELS:
                gaps.add('text_label_limit'); break
            if (any(child.tag!='{'+SVG_NS+'}tspan' or len(child) or
                    set(child.attrib)-{'{http://www.w3.org/XML/1998/namespace}space'} for child in node) or
                    node.get('rotate') is not None):
                gaps.add('positioned_or_styled_text_runs_not_resolved'); continue
            try:
                size = _length(current.get('font-size','16'))
                x,y = _length(node.get('x','0')),_length(node.get('y','0'))
                x += _length(node.get('dx','0')); y += _length(node.get('dy','0'))
                width = _length(node.get('textLength')) if node.get('textLength') is not None else len(text)*size*AVERAGE_CHAR_WIDTH_FACTOR
                if size<=0 or width<0:
                    raise ValueError('Invalid text size')
            except ValueError:
                gaps.add('unsupported_text_length'); continue
            anchor = current.get('text-anchor','start')
            if anchor not in ('start','middle','end'):
                gaps.add('unsupported_text_anchor'); continue
            left,top,right,bottom = 0.,-size*.8,width,size*.25
            if font_metrics is not None and node.get('textLength') is None:
                try:
                    metrics = tuple(font_metrics(text,size,current))
                    if len(metrics)!=5 or not all(math.isfinite(v) for v in metrics):
                        raise ValueError('Invalid font metrics')
                    advance,left,top,right,bottom = metrics
                    if advance<0 or right<left or bottom<top:
                        raise ValueError('Invalid font bounds')
                    width = advance
                    measured_labels += 1
                except (ValueError,TypeError,RuntimeError,AttributeError):
                    gaps.add('font_metrics_fallback')
            x -= width * {'start':0.,'middle':.5,'end':1.}[anchor]
            points = [_apply_matrix(matrix,a,b) for a,b in ((x+left,y+top),(x+right,y+top),(x+left,y+bottom),(x+right,y+bottom))]
            if not all(math.isfinite(v) for point in points for v in point):
                gaps.add('nonfinite_text_bounds'); continue
            quads.append(points)
            boxes.append((text[:128],(min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points))))
            continue
        stack.extend((child,matrix,current,depth+1) for child in reversed(node))
    findings = []; pairs = 0
    for i,(a,box_a) in enumerate(boxes):
        for j,(b,box_b) in enumerate(boxes[i+1:],i+1):
            if pairs>=MAX_PAIR_TESTS:
                gaps.add('text_pair_limit'); break
            pairs += 1
            if _boxes_overlap(box_a,box_b) and _quads_overlap(quads[i],quads[j]):
                if len(findings)<MAX_FINDINGS:
                    findings.append(dict(check='svg_text_overlap',severity='warning',texts=sorted([a,b]),
                        message='Text bounds overlap. Inspect these labels in the rendered sheet before moving them.'))
                else:
                    gaps.add('text_finding_limit')
        if pairs>=MAX_PAIR_TESTS:
            break
    line_gaps.update(gaps)
    def emit_line(finding):
        if len(findings)<MAX_FINDINGS:findings.append(finding)
        else:line_gaps.add('text_line_finding_limit')
    line_geometry=techdraw_svg_lines.check(boxes,strokes,line_gaps,emit_line,quads=quads)
    line_geometry['glyph_metrics_measured'] = bool(boxes) and measured_labels==len(boxes)
    return dict(findings=findings,line_geometry=line_geometry,coverage_complete=not gaps,coverage_gaps=sorted(gaps),
        labels_checked=len(boxes),pair_tests=pairs,glyph_metrics_measured=bool(boxes) and measured_labels==len(boxes),
        measured_labels=measured_labels,estimated_labels=len(boxes)-measured_labels,
        decimal_point_labels=list(dict.fromkeys(text for text,box in boxes
            if re.search(r'(?<![\w.])\d+\.\d+(?![\w.])',text)))[:32],
        conformance_certified=False)


def find_overlaps(svg_bytes):
    """Compatibility helper returning findings only; use analyze_svg for coverage."""
    return analyze_svg(svg_bytes)['findings']
