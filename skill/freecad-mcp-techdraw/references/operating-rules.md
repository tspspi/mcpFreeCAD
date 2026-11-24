# Operating scope

Confirm the intended bridge, document and source objects before editing. Name
the owning document/page explicitly; refresh state after a reopen or restart.
Preserve the source model when changing its drawing. Use explicit MCP tools
first, and keep drawing-scoped scripts on the GUI thread unless the operation
is known to be safe in the background.

For each required feature, identify its owning model/drawing object and the
check that proves it. Correct defects, rerun affected checks and inspect the
updated MCP image. For sustained work, keep a compact local progress note with
the scope, completed checks, remaining requirements and next step; do not put
test journals or private model details into the skill.
