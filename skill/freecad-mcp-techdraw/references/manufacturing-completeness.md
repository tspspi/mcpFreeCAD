# Check manufacturing completeness

Use this procedure for workshop/manufacturing drawings. A plausible projection,
a list of dimension values, or zero checker warnings is not sufficient.

## 1. Inventory the actual part

Inspect the existing source model, topology and feature history. Confirm a valid
nonempty shape before making drawing views. Record its revision and coordinate
frame. Read the final shape as well as history: a pocket's stored length does
not determine its depth when its mode is ThroughAll or up-to-face.

List functional features, not every topological edge. Seam edges on cylinders,
tangent boundaries and equivalent projections do not each need a dimension.

| Feature family | Information to account for |
| --- | --- |
| Prismatic envelope | Overall length, width and thickness; relevant flatness/orientation requirements |
| Round/turned envelope | Each diameter, axial segment length, shoulder location and relevant axis/fit requirements |
| Hole or bore | Diameter, both in-plane location constraints or an unambiguous pattern rule, quantity, through/blind termination and depth where applicable |
| Pocket, slot or recess | Length/width/radius, location, orientation, depth and termination |
| Step or internal shoulder | Size, position, depth and a section/view exposing it unambiguously |
| Groove, chamfer or fillet | Applicable width/depth/angle/radius and its location |
| Thread | Approved designation, pitch, class and extent; conventional representation or an explicit capability gap |
| Assembly/interface | Part identification, mating references, necessary fits and assembly requirements |

Do not infer equality, symmetry, concentricity, through-holes or a repeated
pattern merely from appearance. Recover geometry from the model and specify the
relationship on the drawing. A view's centre is not automatically a datum.

## 2. Make a coverage table

Before adding dimensions, make a compact table with these columns:

```
Feature | source measurement/reference | required size/location/depth |
selected view/callout | drawing object(s) | verified / missing / design decision
```

For each row:

1. Read the model value or relationship independently of its rendered text.
2. Choose a true-shape orthographic/auxiliary view or a section for hidden depth.
3. Create a semantic measured dimension or the appropriate supported callout.
4. Read back its raw value, reference, units, displayed symbol and limits.
5. Inspect the image to confirm the dimension communicates that feature.
6. Mark verified only when all required components of the row are specified.

A circle's diameter does not locate its centre. One centre distance does not
usually supply both in-plane location constraints. Two holes that look alike
need their equality/quantity and pattern locations specified, or separate
measurements. A width in an elevation of a shaft is not a symbol-bearing
diameter dimension. An isometric projected length is not a true model length.

## 3. Check a simple plate systematically

For a model with a 60 × 40 × 12 mm envelope and three through-holes, suppose
inspection gives these centres relative to the lower-left reference corner:

| Hole | Diameter (mm) | X (mm) | Y (mm) | Termination |
| --- | ---: | ---: | ---: | --- |
| Left | 8 | 10 | 30 | Through |
| Centre | 12 | 30 | 10 | Through |
| Right | 8 | 50 | 30 | Through |

An adequate geometric definition must account for all three envelope dimensions,
all hole sizes, every location and termination. A top view showing 60, 40, two
diameters and only some X offsets is incomplete even if the page looks tidy.
Use a front/section view for thickness and a clear through-hole indication.
Use inspected centre vertices and a common corner/reference for coordinate
measurements, or a supported explicit hole table/pattern callout.

This example does not supply material, fits or tolerances. Do not copy example
values into another part or infer those specifications from its geometry.

## 4. Resolve manufacturing intent

Separate facts recoverable from geometry from decisions requiring requirements:
material/condition, fits, dimensional/geometrical limits, datum scheme, surface
texture, edge treatment, thread class, inspection requirements and approval.

Read approved specifications or the user's instructions. Preserve unresolved
items as design decisions. Do not invent ISO 2768 classes, H7/g6 fits, aluminium
alloys or thread classes to make a sheet look finished. Display precision alone
is not a tolerance. For a manufacturing handoff, present the prepared geometry
and a concrete list of missing decisions before asking for their resolution.

## 5. Reconstruct the part from the sheet

Review the exported sheet as if the CAD model were unavailable. For every table
row, find the information needed to reconstruct and inspect that feature without
scaling the print or assuming an unstated relationship. Check that no necessary
value exists only on an unreferenced second sheet or an unlabelled view.

Remove redundant controlling dimensions that create contradictory tolerances;
keep required reference/inspection information clearly distinguished. Check the
chosen standard's actual rules, symbols, projection, linework and applicable
edition separately. The automated checker's completeness flags remain false.

Do not present a draft with missing design decisions, unresolved semantic tools
or unreviewed layout as a complete professional manufacturing drawing.
