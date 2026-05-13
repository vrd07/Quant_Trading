---
description: Diagnose and fix a bug given an error message, stack trace, or description.
---

## Instructions

When invoked with an error or bug description:

### Step 1 — Understand
- Parse the error message and stack trace carefully.
- Identify the exact file and line where the error originates.
- Distinguish between the **error origin** and the **root cause** (they are often different).

### Step 2 — Diagnose
- Explain what is going wrong in plain language (1–2 sentences).
- Identify the root cause category:
  - Type error / incorrect assumption
  - Async/await misuse
  - Missing null / undefined guard
  - Off-by-one / boundary error
  - Incorrect dependency or import
  - Race condition
  - Configuration / environment issue

### Step 3 — Fix
- Provide the minimal, targeted fix — do not refactor surrounding code unless it is directly causing the bug.
- Show a before/after diff for all changed lines.
- Explain **why** the fix works.

### Step 4 — Prevent
- Suggest 1–2 ways to prevent this class of bug in future:
  - A test case that would have caught this
  - A TypeScript type or lint rule that would surface it earlier

## Example Usage

```
/fix TypeError: Cannot read properties of undefined (reading 'id')
    at getUserProfile (src/users/users.service.ts:42:18)
```