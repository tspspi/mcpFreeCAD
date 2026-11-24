"""Metric sheet defaults and explicit view bases for TechDraw."""
from datetime import date
from fractions import Fraction
import math

SHEETS = {
    'iso-a4-portrait': ('A4_Portrait_ISO5457_advanced.svg',210.,297.),
    'iso-a4-landscape': ('A4_Landscape_ISO5457_advanced.svg',297.,210.),
    'iso-a3-landscape': ('A3_Landscape_ISO5457_advanced.svg',420.,297.),
    'iso-a2-landscape': ('A2_Landscape_ISO5457_advanced.svg',594.,420.),
    'iso-a1-landscape': ('A1_Landscape_ISO5457_advanced.svg',841.,594.),
    'iso-a0-landscape': ('A0_Landscape_ISO5457_advanced.svg',1189.,841.),
}
DEFAULT_SHEET = 'iso-a4-portrait'
VIEWS = {
    'top': ((0.,0.,1.),(1.,0.,0.)),
    'bottom': ((0.,0.,-1.),(1.,0.,0.)),
    'front': ((0.,-1.,0.),(1.,0.,0.)),
    'rear': ((0.,1.,0.),(-1.,0.,0.)),
    'right': ((1.,0.,0.),(0.,1.,0.)),
    'left': ((-1.,0.,0.),(0.,-1.,0.)),
    'isometric': ((1.,-1.,1.),(1.,1.,0.)),
}


def catalog(App):
    from .techdraw import _template_path
    result=[]
    for name,(template,width,height) in SHEETS.items():
        try:_template_path(App,template);available=True
        except (ValueError,OSError):available=False
        result.append(dict(id=name,template=template,width_mm=width,height_mm=height,
            available=available,standard_family='din-iso',projection_method='first-angle',
            linear_unit='mm',dimension_style='ISO Oriented',conformance_certified=False))
    return dict(default_page_preset=DEFAULT_SHEET,page_presets=result,
        view_presets=[dict(id=name,direction=list(axes[0]),x_direction=list(axes[1]),
            projection_kind='axonometric' if name=='isometric' else 'orthographic') for name,axes in VIEWS.items()])


def sheet_choice(App, template, preset):
    from .techdraw import _template_path
    if template is not None:
        if preset is not None:raise ValueError('Choose an installed template or a sheet preset, not both')
        return _template_path(App,template),None
    preset=DEFAULT_SHEET if preset is None else preset
    if type(preset) is not str or preset not in SHEETS:raise ValueError('Unknown metric sheet preset; use list_techdraw_presets')
    return _template_path(App,SHEETS[preset][0]),preset


def _unit_vector(value):
    if type(value) not in (tuple,list) or len(value)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):
        raise ValueError('Projection axes require three finite components')
    length=math.hypot(*value)
    if not math.isfinite(length) or length==0:raise ValueError('Projection axes must be nonzero and finite')
    return [x/length for x in value]


def view_choice(preset, direction, x_direction):
    if preset is not None and (direction is not None or x_direction is not None):
        raise ValueError('Choose a named view preset or explicit projection axes, not both')
    if direction is None and x_direction is None:
        preset='top' if preset is None else preset
        if type(preset) is not str or preset not in VIEWS:raise ValueError('Unknown view preset; use list_techdraw_presets')
        direction,x_direction=VIEWS[preset]
    elif direction is None:
        direction=(0.,0.,1.)
    normal=_unit_vector(direction)
    right=None if x_direction is None else _unit_vector(x_direction)
    if right is not None and abs(sum(a*b for a,b in zip(normal,right)))>1e-10:
        raise ValueError('The horizontal projection axis must be perpendicular to the view direction')
    return normal,right,preset


def apply_sheet(document,page,template,preset,scale,texts):
    """Remove shipped example specifications before applying actual user data."""
    if preset is None:
        template.EditableTexts=dict(template.EditableTexts,**texts)
        return
    _,width,height=SHEETS[preset]
    if not (math.isclose(float(template.Width),width,abs_tol=1e-6) and
            math.isclose(float(template.Height),height,abs_tol=1e-6)):
        raise ValueError('Installed preset template does not match its declared sheet dimensions')
    ratio=Fraction(scale).limit_denominator(10000)
    scale_text=(f'{ratio.numerator} : {ratio.denominator}' if ratio.numerator and
        math.isclose(float(ratio),scale,rel_tol=1e-12,abs_tol=0.) else f'{scale} : 1')
    values={key:'' for key in template.EditableTexts}
    defaults=dict(title=document.Label,date_of_issue=date.today().isoformat(),language_code='EN',
        document_status='Draft',scale=scale_text)
    values.update({key:value for key,value in defaults.items() if key in values})
    values.update(texts)
    template.EditableTexts=values
    for name,value in (('MCPDrawingProfile',preset),('MCPStandardFamily','din-iso'),
            ('MCPLinearUnit','mm'),('MCPProjectionMethod','first-angle'),('MCPDimensionStyle','ISO Oriented')):
        page.addProperty('App::PropertyString',name,'MCP TechDraw');setattr(page,name,value)
    page.addProperty('App::PropertyBool','MCPSheetNumberAutomatic','MCP TechDraw')
    page.MCPSheetNumberAutomatic='sheet_number' not in texts
    if hasattr(page,'ProjectionType'):
        if 'First Angle' not in page.getEnumerationsOfProperty('ProjectionType'):
            raise ValueError('This FreeCAD version cannot apply the first-angle page convention')
        page.ProjectionType='First Angle'
    pages=[obj for obj in document.Objects if obj.TypeId=='TechDraw::DrawPage']
    for index,current in enumerate(pages,1):
        if getattr(current,'MCPSheetNumberAutomatic',False) and current.Template is not None:
            fields=dict(current.Template.EditableTexts)
            if 'sheet_number' in fields:
                fields['sheet_number']=f'{index} / {len(pages)}';current.Template.EditableTexts=fields
    # Declare the linear unit on the sheet without editing global preferences.
    note=document.addObject('TechDraw::DrawViewAnnotation',page.Name+'Units')
    note.Text=['Dimensions in mm.'];page.addView(note);note.X,note.Y=60.,height-18.;note.TextSize=3.5
    page.addProperty('App::PropertyLink','MCPUnitsAnnotation','MCP TechDraw');page.MCPUnitsAnnotation=note


def profile_details(page):
    name=getattr(page,'MCPDrawingProfile',None)
    if name is None:return None
    return dict(preset=name,standard_family=getattr(page,'MCPStandardFamily',''),
        linear_unit=getattr(page,'MCPLinearUnit',''),projection_method=getattr(page,'MCPProjectionMethod',''),
        dimension_style=getattr(page,'MCPDimensionStyle',''),conformance_certified=False)
