---
description: Review or generate code through the lens of a specific legendary programmer's philosophy.
---

## Usage

```
/legend <name> [optional: file or code to review]
```

## Available Legends

| Name | Key Lens | Best Used For |
|------|----------|---------------|
| `carmack` | Pure functions, state visibility, static analysis | Reviewing hot paths, async code, stateful logic |
| `prime` | Workflow, DX, vim motions, git hygiene | Reviewing commit hygiene, file structure, dev workflow |
| `tj` | API minimalism, module design, explicit code | Reviewing module interfaces, public APIs, package design |
| `geohot` | Simplicity, minimal deps, understand the stack | Reviewing bloated abstractions, dependency usage |
| `jeffdean` | Scale, distributed systems, latency | Reviewing I/O, service calls, data volume assumptions |
| `torvalds` | Data structures, good taste, no special cases | Reviewing algorithms, data models, function design |
| `dhh` | Convention, monolith, programmer happiness | Reviewing architecture, API design, database usage |
| `blow` | Data-oriented design, perf, no hidden cost | Reviewing performance-critical code, memory layout |
| `mitnick` | Threat modelling, attack surface, zero trust | Reviewing auth, input handling, secrets, permissions |
| `knuth` | Correctness, complexity, invariants | Reviewing algorithms, edge cases, mathematical logic |
| `ritchie` | Unix philosophy, composability, simplicity | Reviewing module composition, interfaces, portability |

## What Happens

When invoked, Gemini will:

1. Read `codinglegits.md` (global) to load the chosen legend's full philosophy.
2. Apply **only that legend's rules and signature question** to the provided code or current file.
3. Output a structured review:
   - **Legend's Verdict** — what they would say in their own voice
   - **Critical Issues** — what they would refuse to merge
   - **Improvements** — what they would want changed
   - **Praise** — what they would approve of (if anything)
   - **The Signature Question** — applied to this specific code

## Examples

```
/legend torvalds src/core/user.service.ts
```
Reviews `user.service.ts` asking: do the data structures eliminate special cases?

```
/legend mitnick src/api/auth.controller.ts
```
Audits the auth controller from a threat modelling perspective.

```
/legend carmack
```
Reviews the current open file through Carmack's state-visibility and pure-function lens.

```
/legend jeffdean src/jobs/sync-worker.ts
```
Estimates the scale characteristics of a background sync job.

## Multi-Legend Review

```
/legend all [file]
```

Runs the code through all 11 lenses sequentially and produces a synthesised report with the **Composite Checklist** from `codinglegit.md` as the final gate.