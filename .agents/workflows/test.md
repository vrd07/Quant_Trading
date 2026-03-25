---
description: Generate comprehensive unit tests for a function, class, or module.
---

## Instructions

When invoked with a target (e.g. `/test src/auth/auth.service.ts`):

1. Read and analyse the target code fully before writing any tests.
2. Identify all functions/methods and their signatures.
3. Generate tests using the project's test runner (default: **Vitest**).

### Coverage Requirements

For each function, generate test cases for:
- ✅ Happy path — normal valid inputs
- ❌ Invalid / malformed inputs
- 🔲 Boundary conditions (empty string, 0, max int, null, undefined)
- 💥 Error paths — what happens when dependencies throw or return errors
- 🔁 Async behaviour — resolved and rejected promises

### Test Style Rules

- Follow **AAA pattern**: `// Arrange`, `// Act`, `// Assert` comments in each test.
- Use `vi.mock()` / `vi.spyOn()` to isolate external dependencies.
- Never test implementation details — test observable behaviour.
- Group related tests under `describe` blocks named after the function.
- Use descriptive test names: `it('returns null when user is not found')`.
- Each test must have exactly **one assertion focus** — split if needed.

### Output

- A complete `.test.ts` file ready to run with no modifications needed.
- A brief note at the top listing any mocks the developer should review for accuracy.