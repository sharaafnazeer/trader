---
name: sdd-write-tasks
description: Break a PRD into independently-grabbable tasks using tracer-bullet vertical slices. Use when user wants to convert a PRD, requirements doc, or plan into tasks (also called issues or work items).
disable-model-invocation: true
license: MIT License, Copyright (c) 2026 ti&m AG, Copyright (c) 2026 Matt Pocock 
---

Break a PRD into independently-grabbable tasks using vertical slices (tracer bullets). Tasks are numbered from `01`, with id `<NN>-<task-slug>`.

## Process

### 1. Gather context

Read the specified PRD from `.sdd/<feature-slug>/prd.md`.

### 2. Explore the codebase (optional)

If you have not already explored the codebase, do so to understand the current state of the code.

### 3. Draft vertical slices

Break the plan into **tracer-bullet tasks**. Each task is a thin vertical slice that cuts through ALL relevant system layers end-to-end across all participating components/services/stacks, NOT a horizontal slice of a single layer or technology.

Number tasks from `01` in topological order (blockers first). The numeric ordinal IS the implementation order — the DAG must agree with `NN` order.

Aim for **3–7 acceptance criteria per task**, each verifiable by an automated test, build/static check, or runtime observation. Reject vague criteria ("works correctly", "is fast").

Define quality gates that are implementation-quality verification checks independent from business acceptance criteria.
Examples of quality gates: compiler warnings, linter violations, ...

### 4. Review tasks

Review the drafted tasks in a separate sub-agent.
They MUST be true vertical slices and NOT horizontal slices (Example: if they have horizontal slice names like frontend-* or backend-*, then they are probably wrong). 
Ensure that they are in dependency order. 
Ensure that the acceptance criteria are clear and verifiable.
Ensure that the quality gates are clear and verifiable.

Rewrite if necessary.

### 5. Write tasks

Write the tasks using the template `task-template` below into a file `.sdd/<feature-slug>/tasks.md`.

### 6. Finish

At the end of the process recommend implementing the feature tasks using the `sdd-implement-task` skill.


## Output format

<task-template>
## Task [NN-task-slug]

A concise description of this vertical slice. Describe the end-to-end behavior, not layer-by-layer implementation.

Avoid specific file paths or code snippets — they go stale fast. Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it here and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

### Implementation steps

- [ ] Step 1
- [ ] Step 2
- [ ] Step 3

### Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

### Quality gates

- [ ] Quality gate 1
- [ ] Quality gate 2

</task-template>