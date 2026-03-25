---
description: Explain a complex piece of code in plain, clear language.
---

## Instructions

When invoked with a file path, function name, or selected code:

### Output Structure

1. **One-line summary** — what this code does in a single sentence.

2. **Purpose & context** — why this code exists. What problem does it solve? Where does it fit in the system?

3. **Step-by-step walkthrough** — explain the logic sequentially. For each meaningful block:
   - What it does
   - Why it does it that way
   - Any non-obvious assumptions or preconditions

4. **Key concepts** — if the code uses a pattern, algorithm, or language feature that a mid-level developer might not immediately recognise, explain it briefly.

5. **Gotchas & edge cases** — anything subtle, surprising, or potentially dangerous about this code that a reader should know.

6. **Suggested questions** — 2–3 questions a developer might ask next (e.g. "How is this called?", "What happens if X is null?") to prompt deeper understanding.

### Tone

- Clear and precise — no jargon without explanation.
- Assume the reader is a competent developer but may be unfamiliar with this specific code.
- No unnecessary padding or filler.

## Example Usage

```
/explain src/core/token-refresh.ts
/explain the `retryWithBackoff` function
```