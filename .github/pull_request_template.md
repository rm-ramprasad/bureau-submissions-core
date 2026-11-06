**Decide:** <!-- a business question for the owner, in plain words, or "nothing" -->
**Risk:** <!-- low / medium / high, and why (what could change for users or files) -->
**Merge if CI is green.** <!-- or: wait for X (a /code-review, the owner's answer, a test on rarfincare) -->

---
<details><summary>Details (no need to read)</summary>

## What and why
<!-- The step (issue) and what this PR changes. -->

## How it was checked
- CI: lint, types, layer rules, tests, coverage ≥ gate, secret scan, Database job
- Golden files: **unchanged** / changed (each file and the reason; a changed bureau file needs the owner's OK)
- Workbook tests (local, `-m workbook`): result

## What to try after deploy
<!-- Steps on rarfincare, or "nothing visible". -->

</details>
