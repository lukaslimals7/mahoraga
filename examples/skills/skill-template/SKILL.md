---
name: skill-template
description: Use when writing a new skill for Mahoraga. Copy this folder, rename it, and replace this body.
tags: [meta, skills, template]
---

# Skill template

A skill is one folder with a `SKILL.md`. Mahoraga lists the frontmatter
`description` in the system prompt and hands the model the whole body when
it calls `load_skill` (or when you run `/skill <name>`).

## Frontmatter

Only two fields matter:

- `name` — how the user invokes it. Lowercase, hyphens, no spaces.
- `description` — the one line the model sees every turn. Write it as
  "Use when <trigger>", because that is what the model matches on.

`tags`, `version` and `author` are optional and currently informational.

## Writing the body

- Address the model directly, in the imperative.
- Lead with the trigger: when to use this, when NOT to.
- Keep it under ~150 lines. A skill is loaded on demand, but a wall of text
  is a wall of text.
- Prefer concrete steps and real commands over prose.
- Name the files and paths exactly as they are on disk.

## Installing

```bash
cp -r examples/skills/skill-template ~/.mahoraga/skills/my-skill
$EDITOR ~/.mahoraga/skills/my-skill/SKILL.md
```

Or from the TUI: `/skills import examples/skills/my-skill`.

## Example

```
---
name: commit-message
description: Use when writing a git commit message for this repo.
---

Write one line, imperative mood, under 70 characters.
Prefix with the area: tui:, llm:, policy:, memory:, skills:, docs:.
Do not end with a period. Reference the test that guards the change.
```
