---
name: sdd-implement-task
description: Implement the next pending task for a feature, verify all acceptance criteria and quality gates, then stop and report. Use when user wants to implement the next task or a specific task of a feature.
disable-model-invocation: true
license: MIT License, Copyright (c) 2026 ti&m AG
---

Implement one task for a feature — finish it end-to-end, verify all acceptance criteria, then stop and report back.

If no feature is specified, ask the user which feature to implement.

## Process

### 1. Gather context

Read the PRD from `.sdd/<feature-slug>/prd.md`.
Read the specified tasks from `.sdd/<feature-slug>/tasks.md`.

### 2. Pick next task

Pick the next task to implement. This should be the first incomplete task in the list.

### 3. Implement

- Touch only what the task requires.
- Follow project conventions (global instructions, existing patterns).
- Write tests where the PRD's Testing Decisions section calls for them.
- Run the build and tests after completing each implementation step.
- If the build or tests fail, fix the failure before continuing to the next step.
- Mark each step in the task's implementation steps as you complete it in `.sdd/<feature-slug>/tasks.md` with `- [x]`.

### 4. Verify acceptance criteria

- Walk each acceptance criterion.
- Demonstrate it passes with concrete evidence: test output, command output, or a manual check with an explicit result. If a criterion requires running the UI in a browser and you cannot do so, state that explicitly — do not mark it verified.
- Only mark a criterion `- [x]` after producing that evidence. Never mark a criterion based on code inspection alone.

### 5. Run quality gates

- Run each command listed in the task's Quality gates section.
- If any gate fails, fix and re-run before proceeding.
- Only mark a gate `- [x]` after the command actually ran and passed. Never mark a gate without running it.

### 6. Report completion

Report to the user with a summary table of every acceptance criterion and quality gate — showing its actual status:
- **Passed** — ran and verified with concrete evidence
- **Failed** — ran but did not pass
- **Skipped** — could not be verified (e.g. browser required but unavailable); state the reason explicitly

Never omit criteria or gates. Never upgrade a Skipped or Failed item to Passed. End the report with this exact line so callers can detect completion:

```
TASKS REMAINING: <N>
```

Where `<N>` is the count of `## Task` headings in `tasks.md` that still have any unchecked implementation steps, acceptance criteria, or quality gates.



