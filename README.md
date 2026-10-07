# Project One — Django E-commerce

Project One is a backend-heavy Django/DRF modular monolith for building and
demonstrating reproducible engineering evidence. The narrow business loop has
a Mock Payment baseline, inventory reservation/commit, cancellation/refund
simulation, and simulated fulfilment. Redis work is temporarily closed at
Phase 4J. Earlier Redis/payment work was committed and pushed to main on
2026-10-05 (`2dacc9a`). The C4/5D increments and Phase 6A checkout baseline have been saved in focused
commits. On 2026-10-07, Phase 6A passed 10 isolated MySQL target tests and 151
related ordering/payment regression tests. Phase 6B now consumes qualification on
normal payment and releases it on genuine unpaid closure; paid cancellation
retains consumed qualification, and late O1 events preserve O2. Its 11 lifecycle
targets and 171 promotions/ordering/payment regression tests passed; 6 explicit
lock experiments were skipped and 12 RabbitMQ tests excluded. This run did not
repeat real MQ/Redis verification. Flash-sale checkout HTTP and frontend entry
points remain absent. See the [learning guide](docs/learning/phase-6b-flash-sale-lifecycle-study-guide.md)
and [verification record](docs/verification/phase-6b-flash-sale-lifecycle.md).
After the RabbitMQ/Linux Worker baseline, bounded retries and manual publication
recovery, Phase 5C-4 adds a persisted processing lease and explicit single-run
`--recover-stale` repair. It checks the original claim version in a short
transaction and skips an Event locked by an active business transaction.
Real isolated Worker kills exercise claim, rollback and commit-before-ACK
windows, including one late-payment refund under original-message redelivery.
MySQL retains the total three-attempt budget and 5/10-second transient-failure
gates. The [learning guide](docs/learning/phase-5c4-payment-worker-recovery-study-guide.md)
and [verification record](docs/verification/phase-5c4-payment-worker-recovery.md)
describe the implementation and evidence. The synchronous baseline remains the
default; periodic recovery and lease heartbeats are absent. New payment migrations
have only been exercised in isolated tests, not applied to the development database.

As of 2026-10-05, RabbitMQ + Celery is required for the payment track, with its
baseline, bounded retries, manual publication and expired-claim recovery slices verified. Its full scope is asynchronous processing of durable Payment Inbox
events, bounded retries, recovery, and real fault verification. MySQL transactions,
state checks, and idempotency remain responsible for business correctness.
Third-party payment Sandbox integration is a No-Go for this iteration; only a
new explicit owner decision may reopen it. Outbox, search, and distributed locks
remain conditional. See `PROJECT_CHARTER.md` and `CONTEXT.md` for current scope.

## Foundation layout

- `backend/config`: Django settings, root URLs, and health check.
- `backend/apps/accounts`: custom user, Session/CSRF authentication, and owned
  Address models/rules/APIs.
- `backend/apps/catalog`, `inventory`: public Category/Product/SKU browse APIs
  and the baseline SKU inventory model.
- `backend/apps/ordering`: cart, order snapshots, inventory reservation,
  cancellation/expiration, and simulated fulfilment.
- `backend/apps/payments`: Mock Payment, Event Inbox, reconciliation, refund
  simulation and opt-in RabbitMQ/Celery event processing.
- `frontend`: Vue 3 + TypeScript/Vite customer UI for the narrow business loop.
- `tests`: pytest/pytest-django tests that use an isolated MySQL database.
- `compose.yaml`: MySQL 8.4 on `127.0.0.1:3307`, optional Redis profiles and
  separate local/test RabbitMQ + Linux Worker profiles.

## Local setup on Windows PowerShell

Create and activate the project virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements\dev.txt
```

Start the isolated MySQL service and wait for it to become healthy:

```powershell
docker compose up -d db
docker compose ps
```

Run the migration and verification commands:

```powershell
python backend\manage.py migrate
python backend\manage.py check
python -m pytest
python backend\manage.py runserver
```

Then visit `http://127.0.0.1:8000/health/`. A successful response is:

```json
{"status": "ok", "database": "ok"}
```

## Configuration safety

`.env.example` documents supported environment variables without containing real
secrets. `.env` is ignored by Git. The checked-in defaults are disposable local
study credentials and the database port is bound to localhost only. Staging and
production refuse the local default Django secret and database password.

Pytest may create and destroy only the configured database whose name starts
with `test_`. The default is `test_project_one`; do not point it at development
or production data.

## Track B: local product detail cache

Product detail has an optional Cache-Aside experiment. MySQL checks Product and
Category visibility on every request; Redis holds only the public JSON response.
Orders continue to use MySQL prices and inventory. Caching is disabled by default
and is currently restricted to development/test environments.

Phase 4B captures a random catalog generation before reading detail data and
includes it in cache keys. Successful catalog saves rotate the generation after
commit: late old refills remain in the old namespace and cannot affect later
requests. Rotation invalidates all detail entries logically, with old entries
expiring by TTL; Redis rotation failure is still a documented consistency limit.

Phase 4D shares in-flight public detail loads within one process, keyed by the
full generation-specific detail key. Each request still checks MySQL visibility.
Followers wait up to 200 ms before independent fallback; unavailable initial
cache reads bypass sharing. This does not coordinate multiple server processes
or by itself protect MySQL from a Redis outage. See the
[singleflight study guide](docs/learning/phase-4d-detail-singleflight-study-guide.md)
and [real comparison results](docs/verification/phase-4d-detail-singleflight.md).

Phase 4E adds two process-local admission slots for degraded public detail loads.
When no slot is free, this endpoint returns 503 immediately. Initial generation
failure is rejected before product SQL; a later payload GET failure can only
protect the subsequent SKU load. Disabled/transactional bypasses are unchanged.
Two slots are a local experiment value, not a database capacity claim. See the
[failure admission guide](docs/learning/phase-4e-fallback-admission-study-guide.md)
and [controlled experiment](docs/verification/phase-4e-fallback-admission.md).

Phase 4F adds real slow-fault and recovery experiments without changing business
behavior. Repeated Redis timeouts still delay rejected requests; a healthy-cache
loader exceeding the 200 ms wait causes independent reloads; failed post-commit
rotation can expose an old price again after Redis recovery. These are measured
limits, not fixes or production capacity claims. See the
[slow-fault guide](docs/learning/phase-4f-slow-faults-study-guide.md) and
[raw evidence and reproduction](docs/verification/phase-4f-slow-faults-and-recovery.md).

Install the updated requirements, then start the optional local service:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements\dev.txt
docker compose --profile cache up -d --wait redis
$env:CATALOG_CACHE_ENABLED = 'true'
.\.venv\Scripts\python.exe backend\manage.py runserver
```

This enables caching only in this shell's server process. It does not edit `.env`,
migrate the development database, or change existing MySQL data. Default Redis
port is `6380`; the cache is disposable and has no persistence volume.

Real integration tests use a separate Redis container on port `6381` and pytest's
isolated MySQL database. Do not run concurrent pytest processes against the same
test database:

```powershell
docker compose --profile cache-test up -d --wait redis-test
$env:PROJECT_ONE_TEST_REDIS_URL = 'redis://127.0.0.1:6381/0'
.\.venv\Scripts\python.exe -m pytest backend\apps\catalog\tests\test_detail_cache.py backend\apps\catalog\tests\test_cache_generation.py -q -s
```

Each test owns a random key prefix and deletes only its own keys, never FLUSHDB.
Without an explicit test Redis URL these integration tests skip; an explicitly
configured but unreachable Redis causes a failure. Never point this setting at
an external or shared service. The failure experiment reserves an unused local
port; it does not shut down Redis or change its global settings.

See [the study guide](docs/learning/phase-4b-cache-generation-study-guide.md)
for the flow and stale-data boundaries, and
[the verification record](docs/verification/phase-4b-cache-generations.md)
for the baseline failure, generation fix, and remaining limitations.

## Guest Catalog Browse UI

See `frontend/README.md` for local startup and
`docs/verification/guest-catalog-browse.md` for the observed verification results,
an isolated browser fixture, the end-to-end request flow, and learning outcomes.

The UI uses native `fetch`, Vue Router, and a development-only Vite `/api` proxy.
It includes Session login, cart and address management, checkout, orders, Mock
Payment initiation/reconciliation, and receipt confirmation. Routine operations
use Django Admin; the customer UI does not yet expose cancellation. The cart does
not lock price or reserve stock; checkout owns order snapshots and reservation.

## MVP-1 Authenticated Cart

The API exposes `GET /api/cart/`, `POST /api/cart/items/`, and
`PATCH/DELETE /api/cart/items/{sku_code}/`. Unsafe requests reuse the existing
Session/CSRF contract. Database constraints enforce one cart per user, one item
per `(cart, sku)`, and quantity 1–99. See
`docs/learning/mvp-1-cart-study-guide.md` for the recommended code-reading order
and `docs/verification/mvp-1-authenticated-cart.md` for observed test evidence.

## MVP-2 Minimum Address

The API exposes `GET/POST /api/addresses/`, `PATCH/DELETE
/api/addresses/{id}/`, and `POST /api/addresses/{id}/default/`. Every lookup is
scoped to the authenticated user. The first address becomes default; changing
the default is transactional; deleting the default promotes the oldest remaining
address. MySQL independently enforces at most one default address per user.

AddressSnapshot is intentionally not part of MVP-2. It will be copied as an
immutable order fact during MVP-3. See
`docs/learning/mvp-2-minimum-address-study-guide.md` for the learning order and
`docs/verification/mvp-2-minimum-address.md` for observed evidence.

## Interview Core execution strategy

- Build the narrow MVP first: browse → login → cart → address → order and
  inventory reservation → Mock Payment → simulated fulfilment → completion.
- Use CRUD Fast Track for ordinary forms, serializers, views, Admin work, and
  thin Vue pages after behavior is confirmed. Keep permissions, ownership,
  database constraints, focused tests, and diff review.
- Study Order/inventory concurrency, Redis cache correctness, and payment
  state/idempotency in depth. These areas are not ordinary CRUD.
- Use Django Admin for routine operations. A complete custom Vue Admin, coupon,
  favorite, review, and search are not Interview Core blockers.
- Consolidate the payment track's RabbitMQ + Celery Inbox processing, bounded
  retries, manual recovery, and real isolated integration/fault evidence. Current
  C1–C4 verification does not imply unattended recovery. Keep MySQL transactions and idempotency as
  the business correctness boundary; Outbox remains conditional.
- Do not integrate third-party payment Sandbox this iteration. Revisit it only
  after a new explicit owner decision; production payments and real money remain
  out of scope.
