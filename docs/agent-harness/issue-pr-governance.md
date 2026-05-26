# Issue and PR Governance

All product refactor work must be managed through GitHub issues and delivered
through pull requests. This applies to architecture, plans, docs, fixtures,
tests, implementation, migrations, UI, backend, and cleanup.

## Issue Rules

- Every task starts with a GitHub issue.
- The product refactor epic is issue #23.
- Each issue must state scope, non-goals, acceptance criteria, validation, and
  business parity expectations when relevant.
- If a task can change architecture, UX, storage, API contracts, or business
  behavior, the issue must include a plan/review section before implementation.
- Do not combine unrelated phases or workflow areas in one issue just because
  they are convenient to edit together.

## Branch Rules

- Branches should map to one issue whenever practical.
- Preferred branch format: `codex/issue-<number>-<short-slug>`.
- Existing branches may continue only if their PR clearly references the
  managing issue.

## PR Rules

- Every repository change lands through a PR.
- PR title or body must reference the managing issue and the product refactor
  epic when applicable.
- PR body must include:
  - scope;
  - plan/review notes for architecture or broad changes;
  - business parity evidence or explanation;
  - validation commands and results;
  - residual risks.
- Implementation PRs must not change strict-parity business behavior unless the
  issue and PR explicitly call out the product decision.
- UI PRs must include browser/Playwright evidence when a runnable UI exists.
- Storage or migration PRs must include rollback or restore evidence.

## Review Gates

Before broad implementation:

1. create or update the issue;
2. add the architecture/plan artifact;
3. run `scripts/harness/check.sh product-refactor-readiness`;
4. open a PR for review;
5. only implement the next slice after the plan is reviewable.

Before merge:

1. confirm the PR maps to the issue scope;
2. confirm acceptance criteria are checked off or explicitly deferred;
3. run the narrow gate plus `scripts/harness/check.sh quick`;
4. verify no generated data, secrets, credentials, or personal local state were
   added;
5. record residual risk in the PR.

## Phase Tracking

Use issue #23 as the high-level progress tracker until a more formal project
board is introduced. Phase issues should reference #23 and link their PRs.
