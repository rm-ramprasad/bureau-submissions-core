# 1. One repository, one package per layer

Date: 2026-11-05 · Status: accepted

## Context
The design (requirements.md §1, §6.4) needs separate, configurable, independently testable layers in a
cloud-neutral core, with a thin shell per cloud. The reference code keeps most of this inside one `submissions`
package.

## Decision
One git repository (`bureau-submissions-core`), a uv workspace with one package per layer under `packages/`
(`bureau_core`, `canonical`, `sources`, `clientconfig`, `app`, `shell_azure`; `shell_aws` later). Each package has
its own version, dependencies and tests. Layer rules are checked by import-linter in CI. A release bundles one
version of each package into the cloud's zip (Function App / Lambda); modules are not deployed as separate services.

## Consequences
- A package can be built, tested and versioned alone; a cloud SDK cannot leak into the core unnoticed.
- One install still runs one zip: no extra cost or moving parts at 5 users per client.
- Cross-package changes land in one pull request.
