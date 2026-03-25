---
description: Generate a Conventional Commits message for the current staged changes.
---

## Instructions

When invoked, analyse the staged diff (via `git diff --staged`) and generate a commit message.

### Rules

1. Use **Conventional Commits** format exactly:
   ```
   <type>(<scope>): <short summary>

   [optional body]

   [optional footer]
   ```

2. Pick the most accurate `type`:
   - `feat` — new feature
   - `fix` — bug fix
   - `docs` — documentation only
   - `style` — formatting, no logic change
   - `refactor` — restructure without feature/fix
   - `perf` — performance improvement
   - `test` — adding/fixing tests
   - `chore` — tooling, dependencies, config
   - `ci` — CI/CD changes
   - `revert` — reverting a commit

3. `scope` = the module, feature, or file area affected (e.g. `auth`, `api`, `deps`).

4. Subject line:
   - Imperative mood: "add", "fix", "remove" — not "added" or "fixes"
   - Max **72 characters**
   - No trailing period

5. Body (include if the change needs explanation):
   - Explain **why**, not what
   - Wrap at 72 characters

6. Footer:
   - Include `BREAKING CHANGE:` if applicable
   - Include `Closes #<issue>` if there's a related issue

### Output

- Print the full commit message in a code block, ready to copy-paste.
- If the diff contains multiple unrelated changes, flag this and suggest splitting into separate commits.
- Refuse to generate a message if secrets or sensitive data appear in the diff — alert the user instead.