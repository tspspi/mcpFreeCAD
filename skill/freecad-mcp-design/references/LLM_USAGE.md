# LLM Usage Guide

This document is an operator-facing resource for configuring an LLM to use `mcpfreecad` productively and safely.

## Intent

The MCP server is designed for an LLM that needs to inspect, create, and modify parameterized FreeCAD models in a running FreeCAD 1.0.2 session.

It is not limited to simple boxes and pockets. The bridge is also intended to support procedural or macro-scale geometry workflows when the explicit high-level tools are not sufficient.

The most reliable workflow is:

1. Inspect the current document and helper availability.
2. Create or activate a target document/body.
3. Prefer mutable parametric features over baked shapes when the model may need later edits.
4. Drive geometry from spreadsheet aliases where possible.
5. Create sketches and immediately validate their solver/closure state.
6. Only then create downstream features such as pads, pockets, or helper-generated parts.
7. Re-inspect the document tree, control the 3D view explicitly, and then capture a snapshot to verify the result.

## Recommended System Prompt Snippet

```text
You are controlling a running FreeCAD session through MCP tools.

Use this operating discipline:
- Inspect before editing: start with bridge_status, list_documents, get_document_tree, and list_helpers.
- Prefer spreadsheet-driven parameters for important dimensions. Use set_spreadsheet_cells first, then bind sketch expressions.
- After every create_sketch call, inspect get_sketch_status. Do not create pads or pockets from a sketch unless solve_status == 0, shape_is_null == false, wire_count >= 1, and at least one wire is closed.
- Prefer create_body + body-contained sketches + create_pad/create_pocket for solid PartDesign workflows.
- Use execute_code only when the explicit tools are insufficient.
- For complex procedural geometry, custom CSG trees, imported macro logic, or algorithmic constructions, `execute_code` is the fallback path.
- When using optional helpers, check list_helpers first and only use helpers whose available flag is true.
- After each modeling step, verify with get_document_tree and optionally capture_snapshot.
- Before taking a snapshot, prefer set_view_orientation, fit_view, and visibility tools so the image is intentional and inspectable.
- If a feature operation fails, inspect the input sketch/body state before retrying.
```

## Tooling Strategy

### Inspection

Use these first:

- `bridge_status`
- `list_documents`
- `open_document` when working from an existing `.FCStd` file
- `get_document_tree`
- `list_helpers`

Use `get_document_tree` whenever you need to know:

- which bodies exist
- which feature is consuming which sketch
- whether spreadsheet objects are already present
- how the current model hierarchy looks

### Working with existing files

When the task starts from an existing model instead of a blank document, prefer explicit file operations.

Recommended sequence:

1. `open_document(path="/path/to/model.FCStd")`
2. `list_documents`
3. `get_document_tree`
4. inspect sketches, spreadsheets, bodies, and features
5. modify the model
6. `save_document(...)`

Important behavior:

- reopening a file may give the document the file-stem name rather than the old in-memory name
- after `open_document`, use the returned `document` field or call `list_documents` again instead of assuming the old document name still applies
- `save_document(path=...)` acts like Save As
- `save_document(document_name=...)` without a path saves back to the document's existing file path
- `close_document(name=...)` closes an open file-backed or transient document

For visual inspection, use:

- `set_view_orientation`
- `fit_view`
- `set_objects_visibility`
- `isolate_objects`
- `capture_snapshot`

Recommended visual loop:

1. Decide what object or body you want to inspect.
2. Hide clutter with `set_objects_visibility` or `isolate_objects`.
3. Set a standard view such as `isometric`, `top`, `front`, or `right`.
4. Call `fit_view`.
5. Call `capture_snapshot`.

### Procedural and macro-style geometry

Some FreeCAD tasks are too specialized for a small fixed MCP tool surface.

Examples:

- algorithmic lattices
- Voronoi-style surface patterns
- custom CSG construction scripts
- geometry imported or adapted from existing macros
- multi-step procedural transformations across several objects

For those cases, prefer this pattern:

1. inspect the current document and helper availability
2. create or open the target document
3. use the explicit MCP tools for the standard parts of the workflow
4. use `execute_code` only for the specialized procedural step
5. return to explicit tools such as `get_document_tree`, `get_sketch_details`, `set_view_orientation`, and `capture_snapshot` to inspect the result

This keeps the complex custom logic local to the part that actually needs it while preserving predictable inspection and validation around it.

### Draft arrays

The MCP surface now supports Draft arrays for common repetition workflows.

Supported modes:

- `ortho`
- `polar`
- `circular`

Recommended use:

- use `ortho` for grid-like placement
- use `polar` for angular repetition around a center
- use `circular` for radial/tangential repetition patterns

Examples:

- `create_draft_array(base_object_name="Box01", array_mode="ortho", vector_x=[10,0,0], vector_y=[0,20,0], count_x=3, count_y=2)`
- `create_draft_array(base_object_name="Box01", array_mode="polar", number=6, angle=180.0, center=[0,0,0])`
- `create_draft_array(base_object_name="Box01", array_mode="circular", number=8, radial_distance=20.0, tangential_distance=8.0, center=[0,0,0], axis=[0,0,1])`

Spreadsheet-driven counts are supported for the relevant count properties:

- `expressions={"count_x":"Params.array_x"}`
- `expressions={"number":"Params.array_n"}`

This is useful when the repetition count should stay parametric instead of being baked into the created array object.

### Optional A2plus assembly workflows

The A2plus workbench is optional. Only use `import_a2plus_part` and `list_a2plus_parts` when those tools are actually present in the MCP tool list for the current machine.

Preferred workflow:

1. create or open the target assembly document
2. save it before the first A2plus import
3. import external components with `import_a2plus_part`
4. inspect imported assembly members with `list_a2plus_parts` and `get_document_tree`

Example:

- `import_a2plus_part(path="/path/to/frame.FCStd", fixed_position=true)`
- `import_a2plus_part(path="/path/to/motor.FCStd", fixed_position=false)`
- `list_a2plus_parts()`

Important notes:

- this surface is GUI-dependent in the current installation
- imported components keep source-file metadata such as `source_file`, `source_part`, `fixed_position`, and `subassembly_import`
- use `get_object_topology` before creating constraints so you can discover valid `FaceN`, `EdgeN`, and `VertexN` names instead of guessing

### Optional A2plus constraint workflows

When A2plus is available, the preferred assembly sequence is:

1. import components with `import_a2plus_part`
2. inspect each component with `get_object_topology`
3. create explicit constraints with `create_a2plus_constraint`
4. inspect resulting mates with `list_a2plus_constraints`

Example:

- `get_object_topology(object_name="ImportedFrame")`
- `get_object_topology(object_name="ImportedMotor")`
- `create_a2plus_constraint(constraint_kind="plane", object1_name="b_part_a_001_", subelement1="Face1", object2_name="b_part_b_001_", subelement2="Face2")`
- `create_a2plus_constraint(constraint_kind="axial", object1_name="b_part_a_001_", subelement1="Edge3", object2_name="b_part_b_001_", subelement2="Edge1")`
- `list_a2plus_constraints()`
- `update_a2plus_constraint(constraint_name="planeCoincident_001", offset=1.0)`
- `delete_a2plus_constraint(constraint_name="axisCoincident_001")`

Supported A2plus constraint kinds currently exposed:

- `pointIdentity`
- `pointOnLine`
- `pointOnPlane`
- `sphereCenterIdent`
- `circularEdge`
- `axial`
- `axisParallel`
- `axisPlaneParallel`
- `axisPlaneAngle`
- `axisPlaneNormal`
- `planesParallel`
- `plane`
- `angledPlanes`
- `CenterOfMass`

If a constraint exposes editable parameters, the MCP result will include them:

- `direction_constraint`
- `offset`
- `angle`
- `lock_rotation`

So the practical edit loop is:

1. `list_a2plus_constraints`
2. `update_a2plus_constraint`
3. inspect again with `list_a2plus_constraints`
4. delete and recreate a mate if the topology choice itself was wrong

### Complex surfaces and sketch-on-surface workflows

For curved faces and other non-planar targets, a planar sketch plus normal pad/pocket is often the wrong abstraction.

Preferred workflow:

1. create or inspect the sketch in its own parametric space
2. attach the sketch to the target face with `attach_sketch_to_face`
3. create a Curves workbench mapping object with `create_sketch_on_surface`
4. inspect the mapped result with `get_document_tree`, visibility tools, and `capture_snapshot`

This is the right path for workflows such as:

- patterns mapped onto cylinders or cones
- procedural perforation skins
- imported or generated irregular meshes
- Voronoi-like or macro-generated surface patterns

Important note:

- the Curves `Sketch On Surface` path is GUI-dependent in this environment because the installed Curves addon imports `FreeCADGui` command registration at module import time
- plan to verify or execute this path in the real running FreeCAD session, not only in headless probes

When you need to inspect an existing sketch rather than create a new one, use:

- `get_sketch_status`
- `get_sketch_details`

### Spreadsheet-Driven Design

Preferred sequence:

1. `set_spreadsheet_cells`
2. `create_sketch`
3. `get_sketch_status`
4. `set_sketch_expression`
5. `create_pad` or `create_pocket`

Example aliases:

- `block_length`
- `block_width`
- `pad_height`
- `hole_radius`

Use expressions like:

- `Params.block_length`
- `Params.block_width`
- `Params.pad_height`
- `Params.hole_radius`

### User-friendly spreadsheets

When creating a spreadsheet that a human may later inspect or edit, make it readable.

Preferred layout:

- one column for a short human label
- one column for the actual parameter value
- aliases attached to the value cells, not the label cells

Example:

- `A1 = Length`
- `B1 = 20 mm`, alias `block_length`
- `A2 = Width`
- `B2 = 12 mm`, alias `block_width`
- `A3 = Height`
- `B3 = 8 mm`, alias `block_height`

This keeps the spreadsheet understandable in the FreeCAD UI while still giving the LLM stable alias names to reference.

### Mutable primitives versus baked shapes

Prefer parametric Part features when later edits are likely.

Preferred:

- `create_parametric_primitive(kind="box", ...)`
- `create_parametric_primitive(kind="cylinder", ...)`
- `create_parametric_primitive(kind="sphere", ...)`
- `create_parametric_primitive(kind="cone", ...)`
- `create_parametric_primitive(kind="torus", ...)`

These create mutable FreeCAD objects such as `Part::Box` and `Part::Cylinder` whose dimensions can stay expression-bound to spreadsheet aliases.

Use `create_primitive` only when you intentionally want a baked `Part::Feature` shape and do not expect to adjust its native dimensions later.

Good rule:

- if the object should remain editable, use a parametric feature
- if the object is just a quick helper solid or disposable geometry, a baked primitive is acceptable

## Sketch Rules

### Closed profiles

For pads and pockets, the sketch must be suitable for downstream solid features.

Check:

- `solve_status == 0`
- `shape_is_null == false`
- `wire_count >= 1`
- at least one entry in `closed_wires` is `true`

### Declarative rectangle behavior

The built-in `rectangle` geometry path now adds:

- 4 line segments
- coincident closure constraints
- horizontal/vertical constraints

This makes it the safest default for rectangular pads.

### Constraint binding

Constraint expressions are currently set with raw property paths such as:

- `Constraints[4]`
- `Constraints[5]`

This is functional but low-level. An LLM should first inspect or know which constraints it created before binding spreadsheet expressions.

### Inspecting an existing sketch

`get_sketch_status` is the fast health check. Use it to answer:

- is the solver clean
- is the sketch fully constrained
- how many degrees of freedom remain
- is there at least one closed wire suitable for a pad or pocket

`get_sketch_details` is the structural inspection tool. Use it to answer:

- which geometry elements already exist
- what order they appear in
- which constraints are already present
- which constraint indices are available for expression binding

Recommended inspection sequence for an existing sketch:

1. `get_sketch_status(sketch_name="BaseSketch")`
2. `get_sketch_details(sketch_name="BaseSketch")`
3. decide whether to add geometry, add constraints, or bind expressions
4. call `get_sketch_status` again before creating downstream features

What `get_sketch_details` exposes:

- `geometry`
  - indexed entries such as `LineSegment`, `Circle`, `ArcOfCircle`, or `BSplineCurve`
  - coordinates such as `start`, `end`, `center`, and `poles` when available
- `constraints`
  - indexed constraint entries
  - constraint type
  - referenced geometry indices and point positions
  - value when the constraint carries one

This means an LLM does not have to guess the current sketch content. It can inspect the exact geometry and constraint list first, then make a targeted edit.

### Editing constraints without raw code execution

Use the first-class constraint tools instead of dropping into arbitrary Python when possible:

- `add_sketch_constraint`
- `delete_sketch_constraint`
- `rename_sketch_constraint`
- `set_constraint_expression`

Recommended pattern:

1. `get_sketch_details(sketch_name="BaseSketch")`
2. inspect geometry indices and existing constraint indices
3. `add_sketch_constraint(...)`, ideally with `constraint_name`
4. if the constraint should have a stable readable name, use `rename_sketch_constraint(...)` or supply `constraint_name` when adding it
5. if the new dimension should stay parametric, call `set_constraint_expression(...)`
5. `get_sketch_status(...)` again before downstream features

Example:

```text
1. Call add_sketch_constraint(sketch_name="BaseSketch", constraint_type="DistanceX", args=[0, 1, 0, 2, 10.0], constraint_name="width").
2. Call set_constraint_expression(sketch_name="BaseSketch", constraint_name="width", expression="Params.block_length").
4. Call get_sketch_status(sketch_name="BaseSketch") to confirm the solver still reports a valid sketch.
```

Prefer constraint names over raw indices when the sketch will stay editable across multiple operations. Indices are still supported, but names are more stable and easier for an LLM to reason about.

### B-spline geometry

`create_sketch` supports declarative B-spline geometry:

```json
{
  "type": "bspline",
  "points": [[0, 0, 0], [10, 5, 0], [20, 0, 0]]
}
```

For a closed periodic B-spline:

```json
{
  "type": "bspline",
  "points": [[0, 0, 0], [8, 4, 0], [16, 0, 0], [8, -4, 0]],
  "closed": true
}
```

Use `get_sketch_details` after creation to inspect the resulting poles and confirm the curve that FreeCAD actually built.

## Reliable Minimum Workflow

This is currently the strongest verified modeling flow:

1. `new_document`
2. `create_body`
3. `set_spreadsheet_cells`
4. `create_sketch` with a `rectangle` on `XY_Plane`
5. `get_sketch_status`
6. `set_sketch_expression` for spreadsheet-linked dimensions
7. `create_pad`
8. `create_sketch` for a circle on an offset plane position
9. `get_sketch_status`
10. `create_pocket`
11. `get_document_tree`

For a spreadsheet-driven parametric primitive instead of a sketch/pad workflow:

1. `new_document`
2. `set_spreadsheet_cells`
3. `create_parametric_primitive`
4. `get_document_tree`
5. `set_view_orientation`
6. `capture_snapshot`

## Optional Helpers

The following helper classes are currently important:

- built-in helpers
  - `threaded_rod`
  - `spur_gear`
- `freecad.gears`
  - `gear_involute`
  - `gear_internal_involute`
  - `gear_bevel`
  - `gear_worm`
- Fasteners
  - `fastener_screw`
  - `fastener_nut`
  - `fastener_washer`
  - `fastener_threaded_rod`

Always check `list_helpers` first. Do not assume an optional helper is available on another machine.

## Known Weak Spots

As of Monday, August 3, 2026:

- `sweep_profile` is not yet reliable in this FreeCAD 1.0.2 setup.
- Constraint binding is still somewhat low-level because it targets raw `Constraints[n]` paths.
- BREP-based library transport may behave differently across machines even though the metadata around it is now versioned and validated.
- Remote HTTP auth structure is implemented, but live end-to-end MCP HTTP validation still deserves a dedicated runtime check.

## Visual Inspection Rules

Snapshots are only useful if the view state is controlled first.

Preferred sequence:

1. `isolate_objects` or `set_objects_visibility`
2. `set_view_orientation`
3. `fit_view`
4. `capture_snapshot`

If you just call `capture_snapshot` against an arbitrary previous camera state, the result may be misleading.

### Imaging all sides of an object

When an LLM needs a complete visual pass around a part, use a standard orientation sweep rather than ad hoc camera moves.

Recommended sequence:

1. identify the object or body with `get_document_tree`
2. hide clutter with `isolate_objects(object_names=[...])`
3. for each view in `front`, `rear`, `left`, `right`, `top`, `bottom`, `isometric`
4. call `set_view_orientation(..., fit_view=true)`
5. call `capture_snapshot(...)`

Suggested filenames:

- `part-front.png`
- `part-rear.png`
- `part-left.png`
- `part-right.png`
- `part-top.png`
- `part-bottom.png`
- `part-isometric.png`

This gives a predictable inspection set that another model or human can review later.

### Visibility control workflow

Use `set_objects_visibility` when you want incremental control:

- hide sketches after they have served their purpose
- hide helper geometry
- temporarily hide imported library objects
- reveal a specific feature for inspection

Use `isolate_objects` when you want a fast “show only these” operation.

Recommended pattern:

1. `get_document_tree`
2. choose object names to inspect
3. `isolate_objects(object_names=[...])`
4. `set_view_orientation(orientation="isometric", fit_view=true)`
5. `capture_snapshot(...)`
6. optionally restore or change visibility with `set_objects_visibility`

## Library Reuse

Reusable library entries are now stored as:

- `shape.brep`
- `metadata.json`

The metadata includes a versioned schema with object and storage details plus caller-supplied `user_metadata`.

Recommended pattern:

1. `store_library_object(...)` with useful `user_metadata`
2. `list_library_objects()` and inspect the `valid` flag
3. only `import_library_object(...)` from entries whose metadata validated successfully

When the reusable object is still buried inside another `.FCStd` file, prefer:

1. `catalog_file_objects(path="/path/to/model.FCStd", include_properties=true)`
2. choose the exact source object by name
3. `store_file_object_to_library(path="/path/to/model.FCStd", object_name="Body", entry_id="body-main")`
4. `list_library_objects()`
5. `import_library_object(entry_id="body-main")`

This gives an LLM a more inspectable and less ambiguous reuse path than a bare geometry dump.

An LLM should therefore prefer:

- body/sketch/pad/pocket workflows
- spreadsheet-driven dimensions
- helper-driven gears/fasteners

and treat sweep-style workflows as experimental until explicitly validated on the target machine.

## Example MCP Usage Narrative

```text
1. Call bridge_status and list_helpers.
2. Call new_document(name="Bracket01") and create_body(name="MainBody").
3. Write spreadsheet aliases with set_spreadsheet_cells.
4. Create a rectangle sketch on XY_Plane inside MainBody.
5. Call get_sketch_status and confirm the profile is closed and solver-clean.
6. Bind spreadsheet aliases to the rectangle constraints.
7. Create a pad.
8. Create a second sketch with a circle for a hole.
9. Validate with get_sketch_status.
10. Create a pocket through all.
11. Inspect the result with get_document_tree and capture_snapshot.
```

For a mutable spreadsheet-driven box:

```text
1. Call new_document(name="ParametricBox01").
2. Write a readable spreadsheet with label cells and aliased value cells.
3. Call create_parametric_primitive(kind="box", name="Box01", expressions={"length":"Params.block_length","width":"Params.block_width","height":"Params.block_height"}).
4. Call get_document_tree to confirm the parametric object exists.
5. Set the view to isometric, fit the view, and capture a snapshot.
```
