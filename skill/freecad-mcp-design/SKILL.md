---
name: freecad-mcp-design
description: Design, modify, inspect, and validate parametric mechanical parts and assemblies in FreeCAD through the mcpfreecad MCP server. Use for FreeCAD modeling, gearboxes, mechanisms, enclosures, fixtures, printable parts, assemblies, fasteners, gears, fit checks, hole validation, and visual CAD inspection. Do not use for tasks that do not involve controlling or reviewing a FreeCAD model.
---

# FreeCAD MCP Design

Use the connected `mcpfreecad` tools to build editable, inspectable, mechanically plausible FreeCAD models.

Read `references/LLM_USAGE.md` when tool details, supported helpers, assembly operations, sketch rules, or known limitations are relevant. Read `references/mechanical-design-rules.md` before designing a mechanism, assembly, gearbox, fastened part, or 3D-printable moving part.

## Operating priorities

Apply these priorities in order:

1. Mechanical correctness and assemblability.
2. Editable and parameterized construction.
3. Verification of topology, motion clearances, openings, and visual appearance.
4. Computational efficiency.
5. Cosmetic detail.

Do not trade mechanical correctness for visual plausibility.

## Start every modeling task

1. Inspect the bridge, open documents, document tree, and available helpers.
2. Identify whether the task modifies an existing document or creates a new one.
3. Establish important dimensions and assumptions before creating detailed geometry.
4. Create a readable spreadsheet with aliases for dimensions likely to change.
5. Prefer mutable parametric features, body-contained sketches, pads, pockets, and expressions.
6. Use `execute_code` only when explicit MCP tools are insufficient or procedural geometry is genuinely the clearer representation.

## Design before detail

For mechanisms and assemblies, first establish:

- component hierarchy
- fixed and moving parts
- axes of motion
- bearing or sliding interfaces
- fastener strategy
- assembly order
- tool access
- required clearances
- expected manufacturing process

Create simplified envelope geometry first when it can expose layout, interference, or assembly problems cheaply.

Do not add modeled screw threads, detailed gears, chamfer decoration, or repeated hardware until the underlying design is verified.

## Sketch and feature discipline

After creating or editing a sketch, inspect it before creating downstream features.

For a pad or pocket profile require:

- `solve_status == 0`
- `shape_is_null == false`
- `wire_count >= 1`
- at least one closed wire

Use named constraints where possible. Bind important dimensions to spreadsheet aliases.

After feature creation, recompute and inspect the document tree. If an operation fails, inspect its body, sketch, support, and topology rather than blindly retrying.

## Mechanical rules

- Model every movable part within its expected range or envelope of motion.
- Verify play-free fits with a default clearance gap of `0.4 mm` unless the user specifies another manufacturing tolerance.
- Treat `0.4 mm` as a default total slicing/fit gap, not a universal engineering tolerance. Increase it where printer/process behavior, long sliding surfaces, misalignment, or assembly angle require more clearance.
- Verify that every intended through-hole, passage, pocket, and cutout is actually open.
- Ensure the design can be assembled in a plausible sequence.
- Leave access for screwdrivers, hex keys, sockets, pliers, nut insertion, and fastener tightening.
- Avoid trapped fasteners and impossible insertion directions.
- Prefer metric fasteners unless explicitly told otherwise.
- Default nuts: DIN 934.
- Default socket-head cap screws: DIN 912.
- Use DIN 7991 countersunk screws where a flush head is required.
- Do not assume every nominal screw hole should equal the screw diameter; distinguish clearance, tapping, press-fit, and locating holes.

## Gears and expensive geometry

Treat gears, threaded fasteners, and repeated booleans as computationally expensive.

- Avoid broad intersection checks involving detailed gears or threaded screws.
- Avoid unions and differences with detailed gears and threaded screws until late in the workflow.
- Add modeled threads only after layout, fit, and assembly have been validated.
- Prefer simplified cylinders or threadless fastener proxies during design.
- External gear geometry may be unreliable in boolean operations.
- When appropriate, create an internal gear or negative tooth-space tool and use it as the cutting geometry instead of unioning an external gear.
- Use cheap analytical checks for gear center distance, pitch compatibility, axial overlap, and envelope clearance before expensive topology checks.

When designing a gear pair, verify at minimum:

- module
- tooth counts
- pressure angle when applicable
- pitch diameters
- center distance
- face width and axial alignment
- backlash/clearance assumption
- shaft and bearing support
- housing clearance
- assembly path

## Visual inspection loop

Visual inspection is mandatory for each completed part and for the assembled model.

Inspect only one camera orientation per tool iteration. Do not queue several orientation changes and snapshots together.

For each part:

1. Isolate the part or relevant body.
2. Set one orientation.
3. Fit the view.
4. Capture one screenshot.
5. Inspect it before changing orientation.
6. Repeat for at least:
   - isometric
   - one orthographic orientation chosen to reveal important holes, interfaces, or internal structure

Use additional front, rear, left, right, top, or bottom views when the geometry is ambiguous.

During visual review, look for:

- missing or blocked openings
- unintended protrusions or voids
- incorrect orientation
- disconnected solids
- impossible assembly features
- insufficient wall thickness
- hidden collisions
- inaccessible screws
- asymmetry that should not exist
- parts that only appear correct from one view

Do not call a part verified merely because a snapshot exists. State what was inspected and what the view showed.

## Verification strategy

Use the cheapest reliable check first:

1. Parameter and formula checks.
2. Sketch solver and closure checks.
3. Bounding boxes, placements, axes, and expected dimensions.
4. Topology and document-tree inspection.
5. Simplified-envelope interference checks.
6. Controlled visual inspection.
7. Exact boolean or intersection checks only when they are necessary and tractable.

For movable assemblies, inspect at least the limiting or critical poses rather than only the neutral pose. Where motion tools are unavailable, calculate and instantiate representative transformed copies or envelopes.

## Drawing handoff

After CAD geometry and mechanical checks pass, the standard recommended next
step is a dimensioned technical drawing before manufacturing preparation. Use
`freecad-mcp-techdraw` and its
[drawing procedures](../freecad-mcp-techdraw/SKILL.md). Identify independent
finished parts, select the necessary views/sections and account for each
functional feature's size, location, depth and intended specification.

Run the drawing checker and inspect its MCP image; correct defects and verify
the saved/reopened references. Keep unresolved materials, fits, tolerances and
approvals explicit. A geometry change invalidates affected drawing evidence.
Respect a model-only deliverable or an explicit request to stop earlier.

## Completion criteria

Do not report the design as complete until:

- important dimensions are parameterized or explicitly documented
- all required solids exist in the expected bodies/components
- sketches used by solid features are valid
- intended holes and passages are open
- moving parts have checked clearance at critical positions
- fasteners and tools have plausible insertion/access paths
- assembly order is plausible
- expensive detailed geometry was postponed until needed
- every part received an isometric and at least one useful orthographic visual inspection
- the assembly received a controlled visual inspection
- dimensioned TechDraw documentation was checked and visually reviewed after CAD validation, or explicitly excluded by the requested scope
- the document was saved when requested

Report assumptions, unresolved risks, skipped expensive checks, and any geometry represented by simplified proxies.
