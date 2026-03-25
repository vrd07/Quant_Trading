---
description: Write or update documentation for a function, module, or API endpoint.
---

## Instructions

When invoked with a target:

### For Functions / Methods — generate JSDoc

```ts
/**
 * [One-line description of what the function does.]
 *
 * [Optional: 1–2 sentence explanation of context or behaviour.]
 *
 * @param paramName - Description of the parameter and any constraints.
 * @returns Description of the return value, including shape for objects.
 * @throws {ErrorType} When and why this error is thrown.
 *
 * @example
 * const result = myFunction('input');
 * // result => { id: 1, name: 'example' }
 */
```

Rules:
- Every public/exported function must have a full JSDoc comment.
- `@param` for every parameter — include type constraints if not obvious from TypeScript.
- `@returns` always present — describe the shape, not just the type.
- `@throws` if the function can throw.
- At least one `@example` for non-trivial functions.

### For Modules — generate a header comment block

```ts
/**
 * @module <module-name>
 *
 * [What this module is responsible for.]
 * [What it is NOT responsible for — clarifies boundaries.]
 *
 * Key exports:
 * - `FunctionA` — brief description
 * - `FunctionB` — brief description
 */
```

### For REST API endpoints — generate OpenAPI-style markdown

```markdown
### POST /api/resource

**Description:** What this endpoint does.

**Auth required:** Yes — Bearer token

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name  | string | ✅ | ... |

**Response 200:**
```json
{ "id": "uuid", "name": "string" }
```

**Errors:**
| Code | When |
|------|------|
| 400  | Invalid input |
| 401  | Missing or expired token |
| 404  | Resource not found |
```

### Quality Rules
- Docs must be **accurate** — read the actual code before writing.
- Do not document the obvious. `// increments counter` on `counter++` is noise.
- Keep docs **close to the code**, not in a separate wiki.