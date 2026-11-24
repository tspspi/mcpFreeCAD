# FreeCAD MCP TechDraw Skill

This Codex skill guides technical drawing creation and review through the
`mcpfreecad` MCP server, with metric DIN/ISO conventions by default and
ANSI/ASME conventions when requested.

## Installation

Global installation:

```sh
mkdir -p ~/.agents/skills
cp -R freecad-mcp-techdraw ~/.agents/skills/
```

Repository-local installation:

```sh
mkdir -p .agents/skills
cp -R freecad-mcp-techdraw .agents/skills/
```

Restart Codex if the skill does not appear automatically.

Invoke explicitly with:

```text
$freecad-mcp-techdraw Create and verify dimensioned drawings of this part.
```

Codex may also select it automatically for tasks matching the description.

## Contents

- `SKILL.md`: operating procedure and completion criteria
- `references/drawing-rules.md`: view, dimension and presentation procedures
- `references/manufacturing-completeness.md`: feature coverage and reconstruction review
- `references/conformance-checks.md`: mandatory scripted and visual validation
- `references/operating-rules.md`: document scope and validation discipline

The skill and checker assist drawing review; they do not certify standards
conformance or supply missing manufacturing specifications.
