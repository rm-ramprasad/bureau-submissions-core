# Reference: documents of the archived `submission-aws-lambda` repository

Copied 2026-11-26 as they were on its final `main` (plus the uncommitted ROADMAP edit of that day). They describe
the **old AWS implementation** (SAM, Lambda, RDS MySQL, `submissions/...` paths) and are kept for reference only:
the design that applies now is in [CONVERGENCE-PLAN.md](../../CONVERGENCE-PLAN.md), [ADR 0004](../../adr/0004-aws-host-and-optional-modules.md)
and [the AWS target](../lms-partnership-architecture.md). Relative links inside these files point to the old
repository and may not resolve here.

| File | Still useful for |
|---|---|
| ARCHITECTURE.md, PIPELINE.md | Request flow, pipeline layering, UI pages: input to `shell_aws` (plan step 9) |
| ROADMAP.md | "Inputs needed" (real bureau values, CIC sample, commercial code questions): still open for every client |
| commercial-and-reference-data.md | How commercial and reference data were designed |
| RUNBOOK.md, ONBOARDING.md | AWS network, deploy and onboarding lessons: input to `shell_aws` and `optional:lms-mapping` |
| CLAUDE-aws-lambda.md | The old repository's working rules ("non-obvious rules" table) |

Bureau specs moved to [docs/specs/](../../specs/): consumer UCRF v3.74, commercial UCRF v3.10.
