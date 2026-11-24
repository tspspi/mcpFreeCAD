# Validate and correct a TechDraw sheet

Use the structural checker and an actual rendered image together. Both can miss
problems. Neither proves DIN/ISO/ASME compliance or complete dimensioning.

## 1. Run the checker

Always call `check_techdraw_conformance(page_name=...)` before declaring a
drawing finished, and rerun it after corrective edits. This is mandatory even
when the sheet looks correct. If unavailable, report incomplete validation. The default also exports SVG
for estimated text-box comparisons and needs snapshot permission. Use
`check_rendered_text=false` for geometry/property checks without SVG export.
Geometry inspection still prepares the current projection in the FreeCAD GUI.

Read these fields before interpreting a clean findings list:

- `geometry_coverage`: which view edge/vertex inventories were fully inspected.
  `geometry_entry_limit` or unavailable GUI leaves part of the page unchecked.
- `rendered_text.performed`, `coverage_complete` and `coverage_gaps`: whether
  SVG text was checked, and which transforms/content/limits prevented coverage.
- `measurement_checks`: numeric agreement for all seven supported dimension
  types, checked/skipped names and calculation sources. Straight reference
  geometry is checked independently in Python; native closest points are an
  explicitly identified fallback for unresolved curved-edge separations.
  Investigate a raw-value mismatch even when FreeCAD reports `isValid=true`.
- `hole_patterns`: verified source-centre/pitch-circle snapshots. Stale,
  missing or altered patterns/linework are errors. This does not certify
  quantity/location/depth completeness or infer a design datum.
- `dimension_layout`: covered linear dimensions, skipped objects and estimated
  bounds. This is not coverage of every line or label style.
- `findings_omitted`: additional findings omitted from the bounded response.
- `error_count`, `warning_count`, `info_count`: triage, then read the findings.
- `blank_title_block_fields`: decide which blanks are acceptable. Never invent
  specifications or signatures to empty this list.

Checks are capped at 256 page objects, 4096 inspected geometry entries, 512
structural findings and bounded SVG nodes/labels/pairs. Reduce sheet complexity
or inspect missing content explicitly when a limit is reached. Zero findings
with incomplete coverage does not mean the page passed.

## 2. Resolve findings against geometry and the image

| Finding | Action |
| --- | --- |
| Editing frames visible | Keep frames enabled only while needed for editing; disable with the page-scoped MCP tool and verify `show_frames=false` before final review/save. |
| Invalid dimension | Repair the referenced source/view geometry; inspect fresh references and verify the resulting measurement. |
| Coincident view anchors | Check whether the overlay is intentional. Otherwise move the views apart and preserve projection alignment. Dimensions attached to different views can legitimately have identical local offsets. |
| Estimated sheet-edge/title-block intersection | Inspect the actual outline, frame and template. Bounding circles can overestimate rectangular views and underestimate unsampled curves; the assumed preset title block is not a measurement of a modified/custom template. |
| Non-preferred scale | Check readability and the governing requirements. Use a suitable preferred ratio when practical; a different ratio is not automatically a defect. |
| ISO/ASME style-family mismatch | Reconcile the requested drawing convention, declared profile and actual dimension style. A style change alone does not change projection method or units. |
| General-tolerance scope unverified | Resolve the approved note against each kind of size, location and angular specification; nonempty title-block text alone does not supply feature-level coverage. |
| Unstated tolerance | Consult functional/manufacturing requirements. Add approved individual limits or an applicable general-tolerance note; decimal precision alone supplies neither. |
| Unreferenced size or circular-feature position | Review the feature checklist across all views. An edge reference does not prove its size/location is fully specified, and equivalent dimensions can exist elsewhere. Add only necessary, nonredundant dimensions. |
| Anchor within estimated view circle | Inspect its actual text, leader and part contour. The circle is a layout heuristic, not the actual silhouette. |
| Nearby elements or similar dimension offsets | Inspect whether labels/lines collide. Dimensions on a shared baseline can be intentional; geometry and dimension type determine the correct placement. |
| Estimated text/extension-line intersection | Inspect the named label and helper-line owner in the PNG. Axis-aligned ISO Oriented dimensions are covered; glyph widths and extension overshoot are estimated. Centre labels within their spans or move labels/lines deliberately. Do not move valid baselines solely to silence a warning. |
| Estimated dimension/extension-line intrusion into title block | Inspect the actual template and whole dimension linework. Preset title-block bounds are assumed, not measured from a modified template. Move the dimension/view when the overlap is real. |
| SVG text overlap | Inspect both labels in the PNG. GUI checks use Qt font ink bounds and advances; `measured_labels`, `estimated_labels` and `glyph_metrics_measured` expose fallback estimates. Bounds do not prove actual glyph intersections. |
| SVG text/line overlap | Inspect the label and stroke in the PNG. Parsed straight strokes and transformed text quadrilaterals include unrelated geometry and template lines. Some crossings are intentional; curved paths and unsupported content are explicit coverage gaps. |
| Geometry/value mismatch or arbitrary value | Refresh and repair references; verify the intended feature and raw measurement. Do not replace the value with arbitrary display text. A valid native dimension can still use the wrong projection component. |
| Duplicate angular unit | FreeCAD appends the degree sign automatically. Remove a literal degree sign from FormatSpec and inspect the export. |
| ISO decimal-marker review | Identify the reported dimension/tolerance labels in the image. Use decimal commas for ISO dimension presentation; identifiers caught by the numeric-label heuristic need separate disposition. Never override measured values or change the host's global locale silently. |
| Mixed dimension text heights | Use one dimension/tolerance lettering height on an ISO sheet and inspect the printed output. Notes and title fields have separate roles. |
| Missing/ambiguous section identification | Match each cutting plane to a uniquely identified resulting section; place its identification above the view and inspect both references. |

Read `rendered_text.line_geometry` separately from text-inventory coverage.
Straight stroke/text intersections are bounded; curved strokes are not checked.
The separate dimension-layout check estimates linear dimension/extension lines
and exposes skipped dimensions. Font bounds depend on the GUI font database and
do not check every shaping/layout feature. Hatching, clipping, symbol meaning
and actual glyph shapes still need visual review. Unsupported content produces
coverage gaps. These checks cannot establish that a sheet is overlap-free.
An actual line through a dimensional value is a defect to correct. The warning
qualification about intentional crossings concerns other SVG content or a
bounding-box false positive, not permission to obscure a dimension.

## 3. Inspect the rendered sheet

Finish editing with `set_techdraw_view_frames(page_name=..., enabled=false)`.
Confirm `show_frames=false` in page readback. Frames can be enabled temporarily
for manual selection/editing; leave them off after finishing and when saving.
This controls editing aids, not the template border.

Request `render_techdraw_page` at a readable resolution and inspect its MCP image.

1. Check the whole sheet for frame/title-block clearance, view arrangement,
   missing views, units, projection symbol and different-scale labels.
2. Inspect crowded areas individually: hole callouts, dimension stacks, section
   labels, notes and leaders. Verify which feature each dimension measures.
3. Check diameter/radius/degree symbols, signed limits, arrows, line weights,
   hidden/centre lines and section hatching at intended print size.
4. Check that each section cuts the intended model-space plane and each detail
   magnifies the intended feature. Their names and anchors alone cannot prove it.
5. After a correction, recheck the affected cluster and the overall layout.
   Change scale, sheet size or sheet count if there is insufficient space.

If you cannot inspect images, leave visual review explicitly outstanding.

## 4. Recheck saved and exported output

Save/reopen the document, read back important measurements and render again.
Verify exported SVG/PDF bytes and digest through the client. Reconcile the final
sheet with the feature checklist and report remaining design questions,
unsupported tools, uninspected content and unresolved findings. Use the
applicable controlled standard/edition and customer specification for a formal
conformance claim; checker counts are not a release decision.
