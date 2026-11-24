"""Private GUI proof of drawing edits, sections/details, checks and exports."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import traceback

import FreeCAD as App
from PySide import QtCore

ROOT=Path(os.environ['MCPFREECAD_TECHDRAW_FIXTURE_DIR']).resolve()
REPO=Path(os.environ['MCPFREECAD_TECHDRAW_FIXTURE_REPO']).resolve()
sys.path.insert(0,str(REPO))
from mcpfreecad.freecad import bridge_server


def run():
    try:
        import Part
        doc=App.newDocument('ReviewedDrawing')
        source=doc.addObject('Part::Feature','ExistingPart')
        source.Shape=Part.makeBox(60,40,12,App.Vector(-30,-20,0)).cut(
            Part.makeCylinder(6,12,App.Vector(0,-10,0)))
        doc.recompute(); original=(source.Shape.hashCode(),source.Shape.Volume)
        def call(command,**params):
            journal=ROOT/'native-last-call.json'
            journal.write_text(json.dumps(dict(command=command,params=params,completed=False)))
            with (ROOT/'native-calls.jsonl').open('a') as stream:stream.write(json.dumps(dict(command=command,params=params))+'\n')
            result=bridge_server._execute_command_impl(command,dict(document_name=doc.Name,**params))
            journal.write_text(json.dumps(dict(command=command,params=params,completed=True)))
            return result
        call('create_techdraw_page',name='Sheet',preset='iso-a3-landscape',editable_texts={'title':'Review fixture'})
        call('create_techdraw_view',page_name='Sheet',name='Top',source_names=['ExistingPart'],view_preset='top',x_mm=100,y_mm=140)
        call('create_techdraw_view',page_name='Sheet',name='Front',source_names=['ExistingPart'],view_preset='front',x_mm=100,y_mm=235)
        geometry=call('get_techdraw_view_geometry',page_name='Sheet',view_name='Top')['geometry']
        edge=next(e for e in geometry if e.get('length_mm')==60.)
        reference={key:edge[key] for key in ('name','reference_token')}
        measured=call('create_techdraw_dimension',page_name='Sheet',view_name='Top',name='Width',
            references=[reference],dimension_type='DistanceX',x_mm=0,y_mm=-40)
        assert measured['dimension']['value']==60.
        updated=call('update_techdraw_dimension',page_name='Sheet',dimension_name='Width',precision=1,
            x_mm=0,y_mm=-50,upper_tolerance=.1,lower_tolerance=-.05)
        assert updated['dimension']['value']==60. and updated['dimension']['format_spec']=='%.1f'
        assert updated['dimension']['upper_tolerance']==.1 and updated['dimension']['lower_tolerance']==-.05
        assert call('set_techdraw_view_frames',page_name='Sheet',enabled=True)['show_frames']
        assert not call('set_techdraw_view_frames',page_name='Sheet',enabled=False)['show_frames']
        retained=[o.Name for o in doc.Sheet.getAllViews()]
        call('update_techdraw_page',page_name='Sheet',editable_texts={'title':'Short title'})
        assert retained==[o.Name for o in doc.Sheet.getAllViews()]
        assert doc.Sheet.Template.EditableTexts['title']=='Short title'
        try:call('update_techdraw_page',page_name='Sheet',editable_texts={'no_such_field':'bad'})
        except ValueError:pass
        else:raise AssertionError('Unknown template key accepted')
        assert doc.Sheet.Template.EditableTexts['title']=='Short title'
        call('update_techdraw_view',page_name='Sheet',view_name='Top',x_mm=105,y_mm=145,scale=1.)
        try:
            call('create_techdraw_dimension',page_name='Sheet',view_name='Top',name='StaleWidth',references=[reference])
        except ValueError:pass
        else:raise AssertionError('Retired view-transform token accepted')
        call('create_techdraw_section_view',page_name='Sheet',base_view_name='Top',name='Section',
            section_normal=[1,0,0],section_origin=[0,0,6],symbol='A',x_mm=265,y_mm=235)
        call('create_techdraw_detail_view',page_name='Sheet',base_view_name='Top',name='Detail',
            anchor_point_mm=[0,-10],radius_mm=8,scale=2.,x_mm=280,y_mm=145)
        assert doc.Section.BaseView is doc.Top and doc.Detail.BaseView is doc.Top
        assert doc.Section.SectionSymbol=='A' and float(doc.Detail.Scale)==2. and float(doc.Detail.Radius)==8.
        doc.Top.Rotation=90.;doc.recompute()
        rotated_edges=call('get_techdraw_view_geometry',page_name='Sheet',view_name='Top')['geometry']
        circle=next(edge for edge in rotated_edges if edge.get('radius_mm')==6.)
        call('create_techdraw_detail_view',page_name='Sheet',base_view_name='Top',name='RotatedDetail',
            anchor_point_mm=circle['center_mm'],radius_mm=7.,scale=1.,x_mm=330,y_mm=90)
        assert math.isclose(doc.RotatedDetail.AnchorPoint.x,10.,abs_tol=1e-8)
        assert math.isclose(doc.RotatedDetail.AnchorPoint.y,0.,abs_tol=1e-8)
        detail_edges=call('get_techdraw_view_geometry',page_name='Sheet',view_name='RotatedDetail')['geometry']
        (ROOT/'rotated-detail.json').write_text(json.dumps(dict(circle=circle,detail_edges=detail_edges,anchor=list(doc.RotatedDetail.AnchorPoint),direction=list(doc.Top.Direction),x_direction=list(doc.Top.XDirection)),indent=2))
        assert any(math.isclose(edge.get('radius_mm',0),6.,abs_tol=1e-8) for edge in detail_edges)
        doc.Sheet.removeView(doc.RotatedDetail);doc.removeObject('RotatedDetail')
        doc.Top.Rotation=0.;doc.recompute()
        # Exercise true measured closest-point distances, not text overrides.
        fresh=call('get_techdraw_view_geometry',page_name='Sheet',view_name='Top')['geometry']
        parallels=[e for e in fresh if e.get('curve')=='Line' and math.isclose(e['length_mm'],60.,abs_tol=1e-8)]
        def ref(row):return {key:row[key] for key in ('name','reference_token')}
        pair=call('create_techdraw_dimension',page_name='Sheet',view_name='Top',name='EdgeSeparation',
            dimension_type='Distance',references=[ref(e) for e in parallels],font_size_mm=3.5)
        assert math.isclose(pair['dimension']['value'],40.,abs_tol=1e-8)
        vertices=call('get_techdraw_view_geometry',page_name='Sheet',view_name='Top',kind='vertices')['geometry']
        centre=next(v for v in vertices if math.dist(v['point_mm'],[0.,-10.,0.])<1e-8)
        bottom=next(e for e in parallels if e['vertices_mm'][0][1]<0)
        mixed=call('create_techdraw_dimension',page_name='Sheet',view_name='Top',name='PointSeparation',
            dimension_type='Distance',references=[ref(bottom),ref(centre)])
        assert math.isclose(mixed['dimension']['value'],10.,abs_tol=1e-8)
        inspected=call('get_techdraw_dimension',page_name='Sheet',dimension_name='EdgeSeparation')['dimension']
        assert inspected['measurement_geometry']['available'] and inspected['font_size_mm']==3.5
        for name in ('EdgeSeparation','PointSeparation'):
            doc.Sheet.removeView(doc.getObject(name));doc.removeObject(name)
        corners=[next(v for v in vertices if math.dist(v['point_mm'],p)<1e-8)
            for p in ([30.,-20.,0.],[-30.,-20.,0.],[-30.,20.,0.])]
        angular=call('create_techdraw_dimension',page_name='Sheet',view_name='Top',name='AngularProbe',
            dimension_type='Angle3Pt',references=[ref(v) for v in corners],precision=1,x_mm=-45,y_mm=-35)
        assert math.isclose(angular['dimension']['value'],90.,abs_tol=1e-8)
        assert angular['dimension']['format_spec']=='%.1f'
        angular_svg=base64.b64decode(call('export_techdraw_drawing',page_name='Sheet',format='svg')['data_base64']).decode()
        assert '90.0°' in angular_svg and '°°' not in angular_svg
        doc.Sheet.removeView(doc.AngularProbe);doc.removeObject('AngularProbe')
        call('create_techdraw_detail_view',page_name='Sheet',base_view_name='Section',name='SectionDetail',
            anchor_point_mm=[0,0],radius_mm=20.,scale=1.,x_mm=330,y_mm=90)
        assert doc.SectionDetail.BaseView is doc.Section
        section_detail=call('get_techdraw_view_geometry',page_name='Sheet',view_name='SectionDetail')
        assert section_detail['returned']>0
        doc.Sheet.removeView(doc.SectionDetail);doc.removeObject('SectionDetail')
        print('Pattern fixture: create source',flush=True)
        pattern_source=doc.addObject('Part::Feature','PatternPart')
        shape=Part.makeCylinder(25,5)
        for x,y in ((15,0),(0,15),(-15,0),(0,-15)):
            shape=shape.cut(Part.makeCylinder(2,5,App.Vector(x,y,0)))
        pattern_source.Shape=shape;doc.recompute()
        call('create_techdraw_view',page_name='Sheet',source_names=['PatternPart'],name='PatternTop',
            view_preset='top',x_mm=350,y_mm=120,scale=2.)
        pattern_edges=call('get_techdraw_view_geometry',page_name='Sheet',view_name='PatternTop')['geometry']
        holes=[e for e in pattern_edges if e.get('curve')=='Circle' and math.isclose(e['radius_mm'],2.,abs_tol=1e-8)]
        print('Pattern fixture: create circle',flush=True)
        pitch=call('create_techdraw_hole_pattern',page_name='Sheet',view_name='PatternTop',name='BoltPattern',
            references=[ref(e) for e in holes])['pattern']
        assert pitch['hole_count']==4 and math.isclose(pitch['pitch_diameter_mm'],30.,abs_tol=1e-8)
        assert pitch['equally_spaced'] and pitch['spacing_deg']==90.
        circle_edges=call('get_techdraw_view_geometry',page_name='Sheet',view_name='PatternTop')['geometry']
        pitch_edge=next(e for e in circle_edges if e.get('curve')=='Circle' and math.isclose(e['radius_mm'],15.,abs_tol=1e-8))
        pitch_dimension=call('create_techdraw_dimension',page_name='Sheet',view_name='PatternTop',
            name='PitchDiameter',dimension_type='Diameter',references=[ref(pitch_edge)],x_mm=-55,y_mm=-30)
        assert math.isclose(pitch_dimension['dimension']['value'],30.,abs_tol=1e-8)
        print('Pattern fixture: check circle',flush=True)
        pattern_check=call('check_techdraw_conformance',page_name='Sheet')
        assert len(pattern_check['hole_patterns'])==1
        assert not any(f['check'].startswith('hole_pattern_') for f in pattern_check['findings'])
        cosmetic=doc.PatternTop.getCosmeticEdge(pitch['cosmetic_tag'])
        assert cosmetic.Format['style']==4
        # Native cosmetic wrappers must not outlive removal of their owner.
        del cosmetic
        print('Pattern fixture: invalidate snapshot',flush=True)
        saved_pattern=doc.PatternTop.MCPHolePatterns
        changed_pattern=json.loads(saved_pattern)
        changed_pattern[0]['center_mm'][0]+=1.
        doc.PatternTop.MCPHolePatterns=json.dumps(changed_pattern)
        print('Pattern fixture: check stale circle',flush=True)
        stale=call('check_techdraw_conformance',page_name='Sheet')
        assert any(f['check']=='hole_pattern_stale_or_unresolved' and f['severity']=='error' for f in stale['findings'])
        doc.PatternTop.MCPHolePatterns=saved_pattern
        call('create_techdraw_annotation',page_name='Sheet',name='Note',text='Review limits remain explicit.',x_mm=110,y_mm=80)
        call('create_techdraw_annotation',page_name='Sheet',name='SectionCaption',text='SECTION A-A',x_mm=265,y_mm=270)
        call('create_techdraw_annotation',page_name='Sheet',name='DetailCaption',text='DETAIL 1   2:1',x_mm=280,y_mm=175)
        call('create_techdraw_annotation',page_name='Sheet',name='PatternCaption',text='HOLE PATTERN   2:1',x_mm=350,y_mm=185)
        report=call('check_techdraw_conformance',page_name='Sheet',check_rendered_text=True)
        assert report['geometry_coverage_complete'] and report['rendered_text']['performed']
        assert report['rendered_text']['glyph_metrics_measured']
        assert not report['conformance_certified'] and not report['feature_completeness_certified']
        assert original==(source.Shape.hashCode(),source.Shape.Volume)
        (ROOT/'report.json').write_text(json.dumps(report,indent=2))
        for format in ('svg','pdf'):
            exported=call('export_techdraw_drawing',page_name='Sheet',format=format)
            data=base64.b64decode(exported['data_base64'])
            assert len(data)==exported['byte_length'] and hashlib.sha256(data).hexdigest()==exported['sha256']
            if format=='pdf':assert data.startswith(b'%PDF-')
            (ROOT/('sheet.'+format)).write_bytes(data)
        preview=call('render_techdraw_page',page_name='Sheet',width=1600,height=1100)
        (ROOT/'sheet.png').write_bytes(base64.b64decode(preview['data_base64']))
        # Verify the projected-coordinate contract under a view rotation.
        before=call('get_techdraw_view_geometry',page_name='Sheet',view_name='Front')['geometry'][0]['vertices_mm']
        doc.Front.Rotation=90.;doc.recompute()
        rotated=call('get_techdraw_view_geometry',page_name='Sheet',view_name='Front')['geometry'][0]['vertices_mm']
        doc.Front.Rotation=0.;doc.recompute()
        doc.saveAs(str(ROOT/'review.FCStd'));App.closeDocument(doc.Name)
        restored=App.openDocument(str(ROOT/'review.FCStd'))
        document_name=restored.Name
        assert not restored.Sheet.ViewObject.ShowFrames
        before_refresh=float(restored.Width.getRawValue())
        reopened=bridge_server._execute_command_impl('check_techdraw_conformance',dict(document_name=document_name,page_name='Sheet',check_rendered_text=True))
        after_refresh=float(restored.Width.getRawValue())
        assert math.isclose(float(restored.PitchDiameter.getRawValue()),30.,abs_tol=1e-8)
        assert len(reopened['hole_patterns'])==1
        assert not any(f['check'].startswith('hole_pattern_') for f in reopened['findings'])
        (ROOT/'reopen-values.json').write_text(json.dumps(dict(before_refresh=before_refresh,after_refresh=after_refresh)))
        assert restored.Width.References2D[0][0].Name=='Top'
        assert math.isclose(after_refresh,60.,abs_tol=1e-8)
        assert restored.Section.BaseView.Name=='Top' and restored.Detail.BaseView.Name=='Top'
        (ROOT/'summary.json').write_text(json.dumps(dict(qualified=True,geometry_preserved=True,
            title_edit_preserves_views=True,unknown_title_key_refused=True,stale_reference_refused=True,
            section_and_detail_persisted=True,dimension_update_persisted=True,pdf_and_svg_verified=True,
            reopened_geometry_coverage=reopened['geometry_coverage_complete'],
            pitch_circle_measurement_and_snapshot_persisted=True,
            pitch_circle_stale_snapshot_rejected=True,
            rotated_detail_targets_inspected_feature=True,
            unrotated_projected_edge=before,rotated_projected_edge=rotated),indent=2))
        if os.environ.get('MCPFREECAD_TECHDRAW_LIVE_TRANSPORT') != '1':
            App.closeDocument(document_name);os._exit(0)
        from http.server import ThreadingHTTPServer
        import threading,time
        bridge_server._bridge_state['dispatcher']=bridge_server._BridgeDispatcher()
        bridge_server.start_bridge_server(host='127.0.0.1',port=0,token='private-review-fixture',create_dispatcher=False)
        http=bridge_server._bridge_state['server']
        serving=bridge_server._bridge_state['thread']
        (ROOT/'bridge-ready.json').write_text(json.dumps(dict(port=http.server_address[1],
            token=http.bridge_token,document=document_name)))
        deadline=time.monotonic()+60.
        def finish():
            done=ROOT/'client-done.json'
            if not done.exists() and time.monotonic()<deadline:
                QtCore.QTimer.singleShot(100,finish);return
            status=1
            try:
                assert done.exists() and json.loads(done.read_text())['qualified']
                assert restored.Sheet.Template.EditableTexts['title']=='MCP reviewed title'
                assert math.isclose(float(restored.Width.getRawValue()),60.,abs_tol=1e-8)
                summary=json.loads((ROOT/'summary.json').read_text())
                summary['serialized_mcp_http_gui_dispatch']=True
                (ROOT/'summary.json').write_text(json.dumps(summary,indent=2));status=0
            except BaseException:
                (ROOT/'error.txt').write_text(traceback.format_exc())
            finally:
                bridge_server.stop_bridge_server();serving.join(2)
                App.closeDocument(document_name);os._exit(status)
        QtCore.QTimer.singleShot(100,finish)
    except BaseException:
        (ROOT/'error.txt').write_text(traceback.format_exc());os._exit(1)

QtCore.QTimer.singleShot(0,run)
