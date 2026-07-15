---
name: kiro-spec-init
description: Initialize a new specification with detailed project description
---


# Spec Initialization

<instructions>
## Core Task
Generate a unique feature name from the project description ($ARGUMENTS) and initialize the specification structure.

## Execution Steps
1. **Check for Brief**: If `.kiro/specs/{feature-name}/brief.md` exists (created by `$kiro-discovery`), read it. The brief contains problem, approach, scope, constraints, and the Spec Size Assessment from the discovery session. Use this to pre-fill the project description and skip clarification questions that the brief already answers.
2. **Run Spec Size Gate Before Writing**: Read `.kiro/steering/spec-sizing.md` if it exists. Validate a recorded assessment against the current description, or perform the assessment now if no brief/assessment exists. If the verdict is `SPLIT_REQUIRED`, or the evidence is insufficient to justify `PASS (single-spec)`, stop before creating or updating spec files and direct the user to `$kiro-discovery "<description>"`.
3. **Clarify Intent**: The Project Description in requirements.md must contain three elements: (a) who has the problem, (b) current situation, (c) what should change. If a brief.md exists and covers these, skip to step 4. Otherwise, ask the user to clarify before proceeding. Ask as many questions as needed; do not fill in gaps with your own assumptions.
4. **Check Uniqueness**: Verify `.kiro/specs/` for naming conflicts. If the directory already exists with only `brief.md` (no `spec.json`), use that directory (discovery created it).
5. **Create Directory**: `.kiro/specs/[feature-name]/` (skip if already exists from discovery)
6. **Initialize Files Using Templates**:
   - Read `.kiro/settings/templates/specs/init.json`
   - Read `.kiro/settings/templates/specs/requirements-init.md`
   - Replace placeholders:
     - `{{FEATURE_NAME}}` → generated feature name
     - `{{TIMESTAMP}}` → current ISO 8601 timestamp
     - `{{PROJECT_DESCRIPTION}}` → from brief.md if available, otherwise $ARGUMENTS
     - `ja` → language code (detect from user's input language, default to `en`)
   - Write `spec.json` and `requirements.md` to spec directory

## Important Constraints
- Do NOT generate requirements, design, or tasks. This skill only creates spec.json and requirements.md.
- Do NOT create files for an oversized or unassessed feature. Spec initialization is an enforcement point even when Discovery was skipped.
</instructions>

## Output Description
Provide output in the language specified in `spec.json` with the following structure:

1. **Generated Feature Name**: `feature-name` format with 1-2 sentence rationale
2. **Project Summary**: Brief summary (1 sentence)
3. **Created Files**: Bullet list with full paths
4. **Next Step**: Command block showing `$kiro-spec-requirements <feature-name>`

**Format Requirements**:
- Use Markdown headings (##, ###)
- Wrap commands in code blocks
- Keep total output concise (under 250 words)
- Use clear, professional language per `spec.json.language`

## Safety & Fallback
- **Ambiguous Feature Name**: If feature name generation is unclear, propose 2-3 options and ask user to select
- **Template Missing**: If template files don't exist in `.kiro/settings/templates/specs/`, report error with specific missing file path and suggest checking repository setup
- **Directory Conflict**: If feature name already exists, append numeric suffix (e.g., `feature-name-2`) and notify user of automatic conflict resolution
- **Write Failure**: Report error with specific path and suggest checking permissions or disk space
- **Spec Size Gate Failed**: Stop before file creation, report the sizing evidence, and suggest `$kiro-discovery "<description>"` for roadmap decomposition
