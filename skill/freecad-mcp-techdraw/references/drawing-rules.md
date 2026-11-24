# Drawing procedures for existing FreeCAD models

Use the governing customer specification and controlled standard/edition when
making a formal conformity claim. These procedures guide tool use and review;
they do not reproduce or replace those standards.

## Choose the drawing convention

1. Preserve the existing model and identify its functional references/features.
2. Default to mm, DIN/ISO, A4 or larger and first-angle arrangement unless the
   user or existing drawing requires another convention.
3. For explicit ANSI/ASME requests, confirm units, sheet format and projection.
   ANSI/ASME does not imply converting mm to inches. Use an appropriate installed
   template and supported dimension styles; report any missing projection/unit/
   symbol controls rather than relabelling an incompatible sheet.
4. Use `list_techdraw_presets` / `list_techdraw_templates` to select real layouts.
   Inspect the actual frame, editable fields and title block. Replace sample
   data with known information or a deliberate blank. Do not invent material,
   mass, tolerance class, approval or release status.

| Sheet | Dimensions in mm |
| --- | --- |
| A4 | 210 × 297 |
| A3 | 297 × 420 |
| A2 | 420 × 594 |
| A1 | 594 × 841 |
| A0 | 841 × 1189 |

Sheet layout: [ISO 5457](https://www.iso.org/standard/29017.html).
Title data: [ISO 7200](https://www.iso.org/standard/35446.html).
Scales: [ISO 5455](https://www.iso.org/standard/11500.html).
For ANSI/ASME, consult the applicable [ASME Y14 standards](https://www.asme.org/codes-standards/y14-standards).
These links identify scope; consult the applicable full text for requirements.

## Arrange views

1. Choose a principal orthographic view showing the functional geometry clearly.
   Add only the views needed to remove ambiguity and specify the part.
2. Align related projections. In first-angle arrangement, top is below front
   and right-side is to its left. In third-angle arrangement, top is above front
   and right-side is to its right. Verify the chosen axes against the actual
   model; world axes need not coincide with the part's functional orientation.
3. Show the correct projection symbol/convention. Do not change a label while
   retaining the opposite view arrangement. See [ISO 5456-2](https://www.iso.org/standard/11502.html).
4. Give views explicit sheet positions. Use orthographic/auxiliary views for
   true-shape dimensions. An isometric projection is explanatory; its projected
   lengths are usually foreshortened.
5. Choose readable scales. The main scale belongs in the title block; label
   views at different scales if not already labelled by the template/view.
   Scale changes must not change the dimension's measured model value.
6. Reserve room for dimensions, leaders, section marks and notes. Inspect the
   actual template/frame; do not rely on a hard-coded title-block rectangle.
   Use a smaller readable scale, a larger sheet or another page when needed.

Named view presets do not create an automatic projection group. Default section/
detail positions are suggested anchors, not an automatic layout or fit guarantee.

## Place views, sections, details and notes on the sheet

View and annotation positions are absolute sheet mm. Projected edge/vertex
coordinates are unscaled view-local mm. Map an inspected local
point to an annotation anchor using:

```
sheet_x = view_x_mm + point_x * view_scale
sheet_y = view_y_mm + point_y * view_scale
```

Example: a view at `(80,130)` with scale `2`, local point `(18,0)`, maps to
sheet `(116,130)`. Offset a nearby note enough to keep text clear of the feature.
Geometry readback has `view_rotation_applied=true`: the returned coordinates
already include the view rotation. Do not rotate them a second time. Confirm
the mapping in the image, and refresh references after a view-transform change.

A dimension's position is different: its `position_frame` is sheet mm relative
to its parent-view centre. To place it at an absolute sheet point, subtract the
parent's sheet position. Do not multiply that offset by the view scale again.
Use readback and rendering to confirm it.

For a section, supply a model-space 3D normal and origin defining the intended
cutting plane. Verify the resulting cut, direction, hatching and symbol in the
render. A 2D sheet point cannot be substituted for the section origin.

For a detail, use the inspected unscaled projected point/radius as the anchor/
radius. Set its magnification and a distinct sheet position. Verify that the
circle and enlarged view identify the intended feature.
FreeCAD clips an already-rotated base source: pass the inspected point directly
without undoing its rotation. Verify rotated-detail targeting in the image.

## Dimension features

Create a feature checklist before creating dimensions. For each functional
feature, record size, location/reference, view, approved tolerance and which
measurement will specify it. Include overall dimensions, interfaces, holes,
patterns, pockets, depths, radii, chamfers and relevant thread information.
Avoid duplicate controlling dimensions across views. Prefer functional datums/
baselines where chained dimensions would accumulate unwanted variation.

1. Fetch all relevant projected geometry pages using `has_more`/`next_offset`.
2. Choose references by geometry type and coordinates, not guessed names.
3. Pass their returned names and `reference_token` to the measured tool.
4. Verify raw value, reference geometry, units, symbols and limits in readback.
5. Inspect the rendered label and leader against the intended feature.

| Measurement | Supported reference selection |
| --- | --- |
| Distance / DistanceX / DistanceY | One straight edge, two vertices, two edges or an edge and a vertex; edge combinations measure closest separation, not centre spacing |
| Radius / Diameter | One projected circular edge |
| Angle | Two projected straight edges |
| Angle3Pt | Three vertices, angle at the middle vertex |

Refresh tokens after source or view-transform changes. Use
`update_techdraw_dimension` to move/restyle an existing valid dimension without
recreating its references. Unsupported semantic dimensions need an explicit gap;
ordinary annotation text cannot replace them.

### Circular features

Look along the cylinder/hole axis to obtain a circular edge for a radius or
symbol-bearing diameter measurement. An oblique or elevation projection can
show an ellipse or straight silhouette; a plain width is not automatically a
diameter callout.

For a location dimension, inspect `center_mm` on the circle, then page through
vertices and look for a matching `point_mm`. If a selectable centre exists, use
its returned name/token with a datum vertex for horizontal/vertical location.
Do not assume every FreeCAD version/view exposes centres as ordinary vertices.
If none exists, inspect available cosmetic/reference geometry capabilities or
report the unsupported centre-reference operation. Never invent a vertex index.
One distance to a centre is not necessarily sufficient to locate it in 2D.

### Pitch circles

1. Inspect the circular feature edges and deduplicate split arcs or concentric
   steps by centre. Confirm which edges belong to actual holes in the model.
2. Use `create_techdraw_hole_pattern` with one inspected edge/token per hole
   (3..64), on one plain projected view. It fits and verifies a circle through
   the centres, checks identical feature sizes and defaults to equal spacing.
   Do not disable the spacing check merely to bypass an unexpected result.
   Compare the selected features with the intended pattern first. For an
   intentionally unequal pattern, explicitly allow unequal spacing and specify
   every angular position.
3. Read back centre, pitch diameter, count, hole size and angles. Refresh the
   view geometry after adding linework. Identify the resulting centre circle
   and create a measured diameter dimension from its returned edge/token.
4. Specify hole size/depth, pattern quantity, spacing and angular orientation
   relative to an identifiable axis/reference. Locate the pattern centre in two
   directions or establish its intended coaxial relationship. A pitch diameter
   alone does not locate the holes. Where an equally spaced circular pattern
   is self-evident and unambiguous, its individual spacing angles can be omitted;
   retain any orientation or offset needed to locate it. Do not give a pitch circle a hole-size
   callout or assume an origin is a manufacturing datum.
5. Run the mandatory checker and inspect the rendered dash-dot centre line,
   hole centre marks, diameter leader and angular dimensions. Pitch circles are
   validated snapshots of inspected source geometry, not automatically updated
   parametric features. Changed/missing references or altered linework require
   rebuilding the pattern and dependent dimensions. Report this limitation.

### Threads and other symbols

Use only the thread designation/class specified by the design. A plain-text
thread note can communicate that designation, but does not supply missing
conventional thread linework or a semantic thread dimension. Preserve the 3D
model; drawing work does not authorize modelling a new hole or thread.

Inspect the live tools for datum symbols, GD&T, hole
callouts, BOMs and other forms. Report missing capabilities rather than
substituting notes without explaining the limitation.

## Apply tolerances and line conventions

For ISO dimension presentation, use a decimal comma for fractional dimension
and tolerance values. Check the actual exported labels: the host locale can
produce decimal points despite an ISO dimension style. Do not change the host's
global locale or substitute arbitrary text for measured values just to make a
label look correct. If the available renderer cannot produce the required
presentation, report it as a remaining output-format limitation.

Keep one lettering height for dimensions and their tolerances on a sheet.
Dimension text must remain readable from the bottom or right and clear of all
crossing lines. Move the text/linework or use a supported interruption of the
extension line; do not accept a line through a dimensional value as cosmetic.
Use matching, unambiguous cutting-plane and section identifiers and place the
section identification above the resulting view. For hatching, use the same
convention across cuts of one part and distinguish adjacent parts.

Distinguish a feature's size from a location distance or angular relationship.
A projected edge/vertex distance verifies nominal geometry; it does not define
the physical size-evaluation operator. In ISO GPS work, do not infer an envelope
requirement, datum precedence, form control or positional tolerance from a size
limit, a model origin or visual symmetry. Establish those specifications from
approved intent and the applicable size/datum/geometrical-specification rules.

Use approved functional/manufacturing requirements for individual limits and
general-tolerance notes. Decimal precision is not a tolerance class. Check
signed lower/upper ordering and whether limits are linear mm or angular degrees.
Preserve datum/fit intent; do not mix ISO GPS and ASME interpretation rules.
[ISO 129-1](https://www.iso.org/standard/64007.html)
identifies dimensional presentation rules;
[ISO 1101](https://www.iso.org/standard/66777.html) identifies geometrical
specification rules. Use the appropriate current full specification when required.

Confirm the governing edition and amendments before using general-tolerance
rules. Do not insert a tolerance class by default or silently migrate an
existing dated requirement. Distinguish feature sizes, location distances and
angular specifications; do not apply one tolerance table indiscriminately.

Keep visible, hidden, centre, dimension and section linework distinguishable.
Choose consistent text height/arrows; verify readability at intended print size.
Keep labels clear of unrelated lines and model geometry. Shared baselines or
chains can be intentional: do not stagger every dimension solely to silence a
position heuristic. Inspect the actual spans and leaders.

## Validate and deliver

Follow [validation and correction](conformance-checks.md). Check size and location
coverage from the feature checklist; the checker cannot determine completeness.
Inspect the MCP image after material layout changes, concentrating on affected
clusters and then the whole sheet. After save/reopen, refresh the projection
through geometry inspection or rendering before accepting measurement readback;
FreeCAD may initially expose an uncomputed raw value. Recheck the measurements.
Export requested SVG/PDF bytes through MCP and verify the received digest.

Use document inspection/readback to confirm sheet numbering and all title data.
Use `update_techdraw_page(editable_texts={...})` for existing title-block fields;
inspect actual keys first. Shorten overflowing text and render again instead of
recreating a dimensioned page just to repair its title.
If a requested edit/delete operation is absent from the live tools, report that
limitation or use an already authorized drawing-scoped scripting capability.
Do not recreate/delete an existing page merely to change a field without
preserving its views/dimensions and considering the user's existing work.

Record the source/drawing revisions, convention/edition, units, projection,
checked requirements and unresolved items. Describe the actual level of review;
style selection and a clean heuristic report do not certify standard conformance
or manufacturing release.
