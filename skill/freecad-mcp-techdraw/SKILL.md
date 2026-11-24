---
name: freecad-mcp-techdraw
description: Create and revise technical drawings of existing FreeCAD models through mcpfreecad, with measured dimensions, annotations, sections, details and visual review for documentation or workshop use. Use metric DIN/ISO conventions by default and ANSI/ASME when explicitly requested. Do not use for CAM programming or changes to the underlying 3D design.
---

# FreeCAD MCP TechDraw

Use the live MCP tools to make a drawing from an existing model. Preserve the
model; changes to the part itself belong to `freecad-mcp-design`. Read
[operating rules](references/operating-rules.md) for operating scope.

## Procedure

1. Establish the drawing contract: source objects/revision, purpose, standard
   family, units, projection method, sheet size, required features and exports.
   Default to metric DIN/ISO, first-angle arrangement and A4 or larger. Follow
   explicit user/customer requirements instead. ANSI/ASME does not imply inches.
   Do not invent tolerances, fits, thread classes, datums or approvals.
2. Inspect the source model, existing pages and `list_techdraw_presets` /
   `list_techdraw_templates`. Confirm that the running tools support the work.
   Distinguish finished independent parts from cutters, imported links and
   intermediate Boolean results. Give independent parts their own drawings;
   do not turn a multi-part file into an unexplained combined part drawing.
   Read [drawing rules](references/drawing-rules.md) before selecting views.
3. Create a sheet with `create_techdraw_page`. Its default is A4 portrait.
   Choose either a named `preset` or an installed `template`. Fill known title
   fields at creation; leave unknown specifications deliberately blank.
   Use `update_techdraw_page` for later title-field corrections when available;
   preserve existing views and dimensions rather than recreating the sheet.
4. Add the minimum source-linked views needed to explain and dimension the
   part. Set explicit positions. Use orthographic views for true-size
   measurements; use isometric views to explain shape. Named view presets do
   not create or arrange a projection group. Omit a view's scale to use page
   scale; label any different scale on the drawing.
5. Build a short feature checklist: for each functional feature, identify its
   size, location/reference, view, required tolerance and intended dimension.
   Cover overall sizes, holes/patterns, pockets, depths, radii and relevant
   interfaces. Avoid duplicate controlling dimensions or dimensioning every
   visible edge. Use [feature procedures](references/drawing-rules.md#dimension-features).
   For workshop/manufacturing work, follow the
   [manufacturing-completeness procedure](references/manufacturing-completeness.md),
   including a source-to-dimension coverage table and reconstruction check.
6. Inspect each chosen view with `get_techdraw_view_geometry`. Page through
   edges/vertices using `has_more` and `next_offset`. Use the returned names
   and `reference_token`; never guess indices. Refresh references after model
   or view-transform changes. Create measured dimensions, then verify raw
   value, units, reference geometry and displayed symbols.
   Use `get_techdraw_dimension` to refresh/read a measurement. Lengths can use
   two vertices, one straight edge, two edges or an edge and a vertex. Edge
   pairs measure closest separation, not hole-centre spacing. Set
   `font_size_mm` when needed for legibility and check it at print size.
   For circular patterns follow the
   [pitch-circle procedure](references/drawing-rules.md#pitch-circles).
7. Add sections/details where necessary. Section normal/origin are model-space
   3D values; a detail anchor/radius use unscaled projected-view millimetres.
   Give each a distinct sheet position and check the resulting geometry.
   Edit views with `update_techdraw_view` and dimensions with
   `update_techdraw_dimension`; preserve valid geometry references.
8. Enable view frames with `set_techdraw_view_frames(enabled=true)` only when
   needed for selection/editing. When edits finish, disable them with
   `set_techdraw_view_frames(enabled=false)` and confirm `show_frames=false`
   in page readback. This does not hide the printed sheet border.
   Scripted validation is mandatory: run `check_techdraw_conformance` with
   rendered-text checking, inspect coverage/skipped cases and resolve errors.
   Then call `render_techdraw_page` and inspect
   the returned MCP image. Follow [validation and correction](references/conformance-checks.md).
   Heuristic warnings are prompts for review, not instructions to add dimensions
   or move geometry blindly. Correct actual defects, recheck affected features
   and repeat the visual review. Report missing coverage explicitly.
9. Save and reopen the document through the available document tools. Refresh
   projections by geometry inspection/rendering before reading measurements. Verify
   view sources, measurements, annotations and the rendered sheet again.
   Export SVG/PDF with `export_techdraw_drawing`; verify received byte length
   and SHA-256. Report unresolved requirements and the level of review performed.

## Coordinate and delivery rules

Projected geometry is in **unscaled view-local mm**. View/annotation positions
are **absolute sheet mm**. Dimension positions are **sheet mm relative to the
parent view centre**; confirm `position_frame` in readback. Linear dimensions/
limits are mm; angular dimensions/limits are degrees. `Angle3Pt` takes three
vertices with the angle at the middle vertex.

`render_techdraw_page` returns an MCP image. Inspect that image even when the
FreeCAD host is remote. SVG/PDF exports carry base64 bytes and metadata over MCP;
they are not PNG previews or host filesystem paths. Use the client's download
capabilities for those files. If image review is unavailable, state that visual
validation remains outstanding.

## Completion criteria

Reconcile every requested feature against the feature checklist and final model.
Check units, projection arrangement/symbol, scale labels, title fields, measured
values, tolerances, glyphs, readable linework, overlaps, clipping and sections.
Keep unknown design intent and unsupported operations visible. Cosmetic text
cannot replace a missing semantic measurement or GD&T tool.

Do not declare the drawing finished without the mandatory checker result and
MCP image review. Rerun the checker after corrective edits; record the disposition
of remaining warnings and coverage gaps. If the checker or image review is
unavailable, explicitly report validation as incomplete.

A clean checker result or an ISO/ASME style selector does not certify compliance.
Distinguish a structurally checked drawing, a visually reviewed drawing and an
approved manufacturing drawing. Claim a particular standard/edition only when
its applicable requirements have actually been verified.
