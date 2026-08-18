# Skill-based Spec Driven Development

## Clarify (Align model understanding)

Make sure you are NOT in auto-pilot mode of `copilot`.

Run `/clear` and execute the following prompt to clarify open questions of the feature and reach shared understanding:

```text
/sdd-clarify @exercise/feature-food-and-macros-tracking.md
```

Answer the clarification questions.

---

## Requirements

Do NOT run `/clear` after the clarify phase.
Execute the following prompt to generate the PRD:

```text
/sdd-write-prd
```

---

## Tasks

After reviewing the PRD,
run `/clear` and execute the following prompt:

```text
/sdd-write-tasks
```

---

## Implementation

After reviewing the task breakdown,
run `/clear` and execute the following prompt:

```text
/sdd-implement-task
```

Repeat this implementation step until all tasks are done.

