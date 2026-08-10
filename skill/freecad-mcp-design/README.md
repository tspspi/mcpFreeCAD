# FreeCAD MCP Design Skill

This Codex skill teaches a repeatable workflow for parametric mechanical design and validation through the `mcpfreecad` MCP server.

## Installation

Global installation:

```sh
mkdir -p ~/.agents/skills
cp -R freecad-mcp-design ~/.agents/skills/
```

Repository-local installation:

```sh
mkdir -p .agents/skills
cp -R freecad-mcp-design .agents/skills/
```

Restart Codex if the skill does not appear automatically.

Invoke explicitly with:

```text
$freecad-mcp-design Design and verify ...
```

Codex may also select it automatically for tasks matching the description.

## Contents

- `SKILL.md`: compact operational workflow and completion criteria
- `references/LLM_USAGE.md`: full MCP operator/tool guide
- `references/mechanical-design-rules.md`: mechanical and computational design policy

The original MCP guide is deliberately kept as a reference instead of being pasted into `SKILL.md`; this preserves context through progressive disclosure.
