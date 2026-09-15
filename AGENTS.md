# Project One Agent Instructions

Read `PROJECT_CHARTER.md` before planning or changing this project. Use `PROJECT_INSTRUCTIONS.md` as the detailed collaboration policy.

## Mission

- Build an interview-facing, backend-heavy Django e-commerce system.
- Prioritize demonstrable technical depth over business breadth or visual polish.
- Business data and integrations may be simulated; technical claims, tests, failures, and measurements must be real and reproducible.
- Keep the system a modular monolith unless the user explicitly changes the charter.

## Non-negotiable Depth Tracks

1. Inventory concurrency and flash sale correctness.
2. Redis cache consistency and resilience.
3. Payment state, webhook handling, and idempotency.

MQ, search, deployment, CI/CD, and performance work must support a real integration or experiment, not merely add dependencies.

## Default Learning Contract

- Default to Learning Mode for new concepts and core business logic.
- The user owns business rules, invariants, expected behavior, architecture decisions, and interpretation of evidence.
- Start with questions or hints, then concepts, pseudocode, minimal local code, and only then a full reference implementation.
- Do not generate an entire core module before the user understands its design.
- Boilerplate and familiar repetitive work may use Pair or Delivery Mode after the user defines behavior.

## Work Protocol

1. Read relevant code, tests, and project documents first.
2. State the business problem and the current vertical slice.
3. Establish a simple baseline before introducing advanced infrastructure.
4. For major decisions, compare alternatives, failure modes, and trade-offs.
5. Implement the smallest coherent change.
6. Run targeted verification and relevant regression tests.
7. Never claim success for tests, deployment, or performance that was not actually observed.
8. Update durable decisions and evidence in repository files, not only in chat.

## Git Workflow — Main First

- This is currently a solo learning project. Default to developing ordinary, low-risk vertical slices directly on `main`.
- Do not automatically create a feature branch, Git worktree, or pull request for every task.
- At the end of a coherent small stage: inspect `git status`, run the relevant tests, stage only the scoped files, create a meaningful commit, and push `main`.
- Keep commits focused. Never mix unrelated user changes into the same commit, and never weaken testing, review, self-checks, CI, or version-control discipline merely because the project is solo.
- Recommend an isolated branch only for large refactors, high-risk database changes, inventory concurrency or flash-sale work, payment state machines or webhooks, uncertain experiments, or other large changes that need isolation and easy rollback.
- Before creating an exceptional branch or worktree, explain why isolation is warranted. After completing it, verify the branch, integrate it safely into `main`, confirm `main` contains the intended commit or equivalent content, and only then consider deleting the branch or worktree.
- Before every push, re-check the current branch, `git status`, the intended diff, and the latest observed test results.

## Testing

- Introduce pytest/pytest-django from the project skeleton.
- Use unit tests for pure rules; isolated MySQL tests for ORM, transactions, constraints, orders, and inventory.
- Use real isolated Redis/RabbitMQ/Elasticsearch services for critical integration semantics.
- Test duplicate, reordered, invalid, concurrent, timeout, and dependency-failure paths where relevant.
- Do not let AI-generated tests and implementation share an unconfirmed definition of correctness.

## Communication

- Explain new terms as `English Term + simple Chinese explanation`.
- Teach request flow from frontend through HTTP, auth, API/domain logic, ORM/storage, and response.
- For commands, explain purpose, expected output, and what different results mean.
- Review with concrete findings, minimal fixes, and verification.
- At each important milestone, require the user to explain why, alternatives, trade-offs, evidence, and likely interview questions.

