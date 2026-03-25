---
description: Refactor selected code for clarity, idiomatic style, or performance — without changing behaviour.
---

Instructions
When invoked with a target file or code block:
Step 1 — Identify Opportunities
Scan for these common refactor targets:

Long functions — extract logical blocks into named helpers
Deep nesting — flatten with early returns / guard clauses
Magic values — replace with named constants or enums
Duplicated logic — extract to a shared utility
Unclear names — rename variables, functions, or types for intent
Bloated parameters — collapse 4+ params into an options object
Implicit types — add explicit TypeScript types where inferred types are opaque
Promise chains — convert .then().catch() chains to async/await
Unnecessary complexity — simplify logic that is more complex than it needs to be

Step 2 — Refactor

Make changes one concern at a time — do not mix rename + extract + restructure in one go unless they're tightly related.
Do not change behaviour. If existing tests break after your refactor, that's a bug.
Preserve all existing public interfaces and exports.

Step 3 — Output

Show a before/after for each change.
Annotate each change with a brief label:

[extract function]
[flatten nesting]
[rename for clarity]
[replace magic value]
etc.


Note if any changes require updating call sites elsewhere.

Step 4 — Test

Confirm existing tests still pass conceptually.
If tests need updating due to a rename, list the files to update.