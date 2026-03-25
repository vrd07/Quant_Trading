---
description: Scaffold a new feature module for the antigravity project.
---

# /new-feature

Scaffold a new feature module for the antigravity project.

## Instructions

When this command is invoked with a feature name (e.g. `/new-feature user-auth`):

1. Ask for any missing context: what does this feature do? Any key entities or external dependencies?
2. Create the following files under `src/` using the feature name as the directory:

```
src/<feature-name>/
├── index.ts              # Public exports only
├── <feature-name>.service.ts   # Business logic
├── <feature-name>.types.ts     # TypeScript types/interfaces
├── <feature-name>.schema.ts    # Validation schemas (zod/yup)
└── <feature-name>.test.ts      # Unit tests skeleton
```

3. Follow these rules when generating:
   - Services return `Result<T, AppError>` — no throwing.
   - Types go in `.types.ts` — never inline in service files.
   - Export only what's needed from `index.ts` (barrel export).
   - Stub at least 3 meaningful test cases in the test file.
   - Add a JSDoc comment at the top of each file describing its role.

4. After scaffolding, print a summary of created files and any TODOs the developer must fill in.

## Example

```
/new-feature payment-gateway
```

Creates `src/payment-gateway/` with all files above, typed for a payment gateway integration.