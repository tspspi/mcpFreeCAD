# TechDraw tools

Use these tools for drawings of existing geometry. Preserve the source model
and identify independent finished parts before selecting views.

| Task | Tools |
| --- | --- |
| Discover sheets | `list_techdraw_presets`, `list_techdraw_templates` |
| Create and inspect pages | `create_techdraw_page`, `update_techdraw_page`, `list_techdraw_pages`, `get_techdraw_page` |
| Place views | `create_techdraw_view`, `update_techdraw_view`, `create_techdraw_section_view`, `create_techdraw_detail_view` |
| Inspect and dimension | `get_techdraw_view_geometry`, `create_techdraw_dimension`, `update_techdraw_dimension`, `get_techdraw_dimension` |
| Add notes and pitch circles | `create_techdraw_annotation`, `create_techdraw_hole_pattern` |
| Review and deliver | `check_techdraw_conformance`, `set_techdraw_view_frames`, `render_techdraw_page`, `export_techdraw_drawing` |

## Coordinates and references

Projected geometry uses unscaled view-local millimetres. View/annotation
positions use absolute sheet millimetres; dimension positions use sheet
millimetres relative to their parent view centre. Angles use degrees.

Page through all required edges and vertices. Pass the returned selection name
and reference token when creating a dimension; refresh after source or view
transform changes. Supported measurements are Distance, DistanceX, DistanceY,
Radius, Diameter, Angle and Angle3Pt. Edge pairs and mixed edge/vertex references
measure closest separation, not hole-centre spacing. Angle3Pt uses its middle
vertex as the angle vertex.

Pitch circles are fitted from inspected circular feature centres. Specify hole
size/depth, quantity, pattern location and angular orientation as required.
The circle is a validated snapshot rather than an automatically recomputed
parametric feature; recheck it after model edits and reopening.

## Review boundary

The checker compares supported measurements to inspected geometry and reports
structural, presentation and layout findings. GUI SVG review uses font bounds
and bounded straight-stroke checks. Curved paths, glyph outlines, full feature
coverage, projection arrangements and physical inspection operators are not
fully assessed. Read the coverage fields; zero errors is not approval.

Metric sheet defaults and ISO dimension styles do not by themselves establish
DIN/ISO compliance. The host's numeric formatting can still produce decimal
points; the ISO review warns about fractional SVG labels requiring decimal
commas. Do not replace measured values with arbitrary text to hide a mismatch.

Semantic GD&T/datum features, conventional thread callouts, surface texture
symbols, richer hole tables, auxiliary/basic-dimension authoring and automatic
complete dimension planning remain outside the current tool surface. Geometry
measurements do not specify material, fits, tolerances, datum precedence, form
control or approval. Establish those from the engineering requirements.

Use the [drawing procedure](../skill/freecad-mcp-techdraw/SKILL.md) to combine
scripted checks, feature reconstruction, image review and save/reopen validation.
