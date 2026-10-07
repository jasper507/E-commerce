# Project One Agent Instructions

## Primary Collaboration Gate — Interview Value First

- At the start of each chat and before proposing another learning slice, design, implementation or experiment, ask: "How does this help the owner prepare for a China backend/software engineering internship or graduate interview and become a stronger engineer?"
- Before advancing, briefly state the concrete skill, interview follow-up it supports, and smallest credible evidence. Supporting work may explain its indirect contribution. A roadmap item or technology name alone is insufficient.
- For comparisons and deeper experiments, explain why these candidates matter, the learning cost and a stopping condition. Once evidence supports the intended explanation, consolidate understanding before adding more experiments; protect time for coding, algorithms and computer science fundamentals.
- Distinguish official hiring requirements, public first-person interview accounts and inference. Do not imply that a specific keyword is mandatory, invent interview frequency or live interviews, or promise hiring outcomes.
- This gate takes precedence over automatic roadmap progression while preserving correctness, safety and honest evidence. When the owner asks to pause and discuss, stop the original slice until explicitly resumed; perform only separately authorized work in that turn.

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

RabbitMQ + Celery payment-event processing is required for Interview Core, as part of the payment depth track. This is confirmed planned scope, not a claim that MQ is implemented or verified. Search, deployment, CI/CD, and performance work must support a real integration or experiment, not merely add dependencies.

## Payment Async Scope — Confirmed 2026-10-01

- Implement RabbitMQ + Celery for payment-event background processing, bounded retries, idempotent consumption, and recovery of unfinished Inbox events. Keep this scope within the payment track; other async scenarios are not automatically required.
- MySQL remains the source of truth for payments, orders, and inventory. Persist and commit the Inbox event in a short transaction before publishing its identifier; never hold database locks while waiting for the broker or gateway.
- Commit payment confirmation, order transition, inventory effects, and event-processing state together. Duplicate delivery or worker interruption must not duplicate payment, inventory, or refund effects.
- Post-commit publication still has a commit-to-publish failure window. Inbox recovery must cover missed publication and unfinished processing; MQ alone is not a recovery guarantee. Retry only eligible transient failures with bounded attempts/backoff; invalid events and exhausted retries must remain visible for investigation.
- Preserve the MySQL/Mock baseline and observe its limitation before integration. Verify the chosen MQ configuration with real isolated RabbitMQ and workers, including duplicate delivery, worker interruption, broker outage, and recovery. Report planned, implemented, and actually verified behavior separately.
- Transactional Outbox remains conditional on demonstrated reliable-publication needs. Search and distributed locks retain their existing gates. Third-party payment Sandbox is No-Go for the current project scope; use the backend Mock Gateway and no real money. Revisit only if the user explicitly changes this decision; do not automatically inspect accounts or start provider integration.

## Interview-First Decision Gate

- The portfolio outcome is **one demonstrable business loop plus three deep interview stories**. The loop is browse → login → cart → address → order/inventory reservation → mock payment/inventory commit → simulated shipping → receipt. The stories are inventory concurrency and flash sale correctness; Redis cache consistency and failure fallback; payment state, webhook handling, and idempotency.
- Every database, code, architecture, infrastructure, or project-scope design must state which part of that outcome it strengthens. Before a significant change, give the business problem, the invariant or failure scenario, the smallest suitable design, one meaningful alternative and trade-off, and the evidence needed to support an interview claim. Supporting work may state this briefly. If a proposal has no clear contribution, defer or drop it.
- For each deep story, preserve a reproducible path from a simple baseline and observed failure or limitation to the chosen implementation, verification, measured result where relevant, and remaining trade-off. Synthetic traffic and mock integrations are acceptable; never present them as production evidence.
- Prefer the next coherent piece of core logic over broadening ordinary commerce features or repeating low-value tests. Verify enough to support the specific claim, then advance. Do not add technology just to list it on a résumé.
- In project progress replies, say where the work sits in the roadmap, how it strengthens the business loop or one of the three stories, and the next concrete step. Keep the plan understandable to a beginner and make the interview explanation something the project owner can give independently.

## Default Learning Contract

- Use Learning Mode for order, inventory, Redis, payment and MQ reliability, concurrency, state machines, idempotency, architecture trade-offs, and interpretation of evidence.
- Use CRUD Fast Track for routine create/read/update/delete flows, Admin registration, serializers, ordinary forms/tables, fixtures, and other familiar boilerplate. After the behavior is confirmed, AI may implement these directly in Pair or Delivery Mode.
- A CRUD fast pass still requires the user to understand the request flow, ownership/permission boundary, important database constraints, one normal example, and one important failure example. It does not require line-by-line study of repetitive framework code.
- The user owns core business rules, invariants, expected behavior, architecture decisions, test oracles, and interpretation of evidence.
- For depth-track work, start with the simplest baseline, reproduce the problem, compare alternatives, implement the smallest coherent change, and then verify it. Do not generate an entire core module before the user understands its design.
- Classify work before implementation as `Depth`, `Supporting`, or `Boilerplate`; do not apply the same learning depth to every file.

## Work Protocol

1. Read relevant code, tests, and project documents first.
   Before generating or modifying the next batch of code, commit and push the previous reviewed changes under the standing authorization below.
2. State the business problem and the current vertical slice.
3. Classify the work as Depth, Supporting, or Boilerplate and choose Learning, Pair, or Delivery Mode accordingly.
4. Establish a simple MVP/MySQL/Mock baseline before introducing advanced infrastructure.
5. For major decisions, compare alternatives, failure modes, and trade-offs.
6. Implement the smallest coherent change.
7. Run targeted verification and relevant regression tests.
8. Never claim success for tests, deployment, or performance that was not actually observed.
9. Update durable decisions and evidence in repository files, not only in chat.

## Git Workflow — Main First

- This is currently a solo learning project. Default to developing ordinary, low-risk vertical slices directly on `main`.
- Do not automatically create a feature branch, Git worktree, or pull request for every task.
- At the end of a coherent small stage: inspect `git status`, run the relevant tests, stage only the scoped files, create a meaningful commit, and push the reviewed current work branch. Ordinary daily development still defaults to `main`.
- Keep commits focused. Never mix unrelated user changes into the same commit, and never weaken testing, review, self-checks, CI, or version-control discipline merely because the project is solo.
- Recommend an isolated branch only for large refactors, high-risk database changes, inventory concurrency or flash-sale work, payment state machines or webhooks, uncertain experiments, or other large changes that need isolation and easy rollback.
- Before creating an exceptional branch or worktree, explain why isolation is warranted. After completing it, verify the branch, integrate it safely into `main`, confirm `main` contains the intended commit or equivalent content, and only then consider deleting the branch or worktree.
- Before every push, re-check the current branch, `git status`, the intended diff, and the latest observed test results.

### Standing Commit/Push Authorization — 2026-10-06

- The project owner authorizes commit and push without repeated confirmation. Before generating or modifying each new batch of code, save the previous reviewed, authorized changes in focused commits and push the current work branch.
- Review scope, remote, tests and sensitive information first. Do not include unknown changes or another chat's unfinished work. If the worktree is clean and the relevant commits are already remote, continue without an empty commit; if only reviewed local commits are pending, push them.
- Confirm the remote branch equals local HEAD before the next code generation. If saving cannot safely complete, preserve the work and report the concrete blocker before proceeding.
- If required verification is unavailable solely because of the environment, record the limitation and push the candidate to an isolated `codex/*` branch. Do not claim it verified or merge unverified high-risk work into `main`.
- Preserve review, testing, focused commits and safe integration requirements. This authorization does not allow force-push, rewriting remote history or destructive Git operations. Details are in `PROJECT_INSTRUCTIONS.md`.

## Testing

- Introduce pytest/pytest-django from the project skeleton.
- Use unit tests for pure rules; isolated MySQL tests for ORM, transactions, constraints, orders, and inventory.
- Use real isolated Redis/RabbitMQ/Elasticsearch services for critical integration semantics.
- Required payment MQ verification must exercise real RabbitMQ and worker behavior; eager-mode tasks, mocks, and static checks alone cannot establish delivery or crash-recovery claims.
- Test duplicate, reordered, invalid, concurrent, timeout, and dependency-failure paths where relevant.
- Do not let AI-generated tests and implementation share an unconfirmed definition of correctness.

## Communication

- Explain new terms as `English Term + simple Chinese explanation`.
- Teach request flow from frontend through HTTP, auth, API/domain logic, ORM/storage, and response.
- For commands, explain purpose, expected output, and what different results mean.
- Review with concrete findings, minimal fixes, and verification.
- At each important milestone, require the user to explain why, alternatives, trade-offs, evidence, and likely interview questions.

