# Project One — Django E-commerce

Project One is a backend-heavy Django/DRF modular monolith for building and
demonstrating reproducible engineering evidence.

Learning documents: the [module reading guide](docs/learning/README.md) links all
33 local study guides. Each now begins with the previous state, the slice's
change, and its resulting limits, with historical stages kept distinct from
current progress. This documentation-only pass is recorded in the
[verification](docs/verification/learning-guide-change-overviews.md).

Product-search baseline (2026-10-09): the public product list accepts optional
`q` for whole-keyword inclusion in name or description (trimmed, max 80), while
preserving visibility, category intersection, 20-item pages and product summaries.
The customer page has a search form and keeps `q` in its URL. Isolated MySQL
targets passed 9 tests; related checks passed 24 tests / 6 subtests with the
explicit experiment skipped, and the frontend build passed. With 5,000 visible
synthetic products, the three complete API requests returned 500/0/250 matches
and executed 2/1/2 SQL statements; their COUNT and list plans are preserved.
This is a functional and query-plan baseline, not a slow-SQL or ES performance
improvement claim. No ES service/dependency or development database change was
made; frontend runtime/browser behavior remains unverified in this slice.
See the [verification](docs/verification/phase-7a-search-baseline.md) and
[study guide](docs/learning/phase-7a-product-search-baseline-study-guide.md).
The proposed next slice is an isolated, rebuildable Elasticsearch search prototype.

Flash-sale Lua admission (2026-10-09): a default-off, local/test-only shared
attempt counter runs before the HTTP lock-wait adapter and MySQL checkout.
Full windows return 429, including original-Key confirmation; unknown Redis
results return 503. Both preserve the original intent for later confirmation.
MySQL still owns orders and inventory; no Redis stock pre-decrement is added.
Real Redis verified 24 simultaneous clients against 4 permits. A controlled
24-customer / 8-thread API batch reached 24 transactions with admission off,
4 with it on, and 20 rejections with no checkout SQL after fixed authentication.
Final checks passed 53 tests / 325 subtests. These are isolated correctness and
protection observations, not production throughput or an in-flight limit.
See the [verification](docs/verification/phase-6f-flash-sale-admission.md) and
[study guide](docs/learning/phase-6f-flash-sale-admission-study-guide.md).
Development runtime data/configuration is unchanged; that stage handed off to
the product-search baseline before the confirmed Elasticsearch work.

Order-list index implementation (2026-10-09): the verified
`(user_id, created_at, id)` index is declared in the model and migration 0005.
Isolated MySQL verified forward/reverse/forward migration, unchanged existing
orders and snapshots, and use of the formal index for first/deep pages. Targets
passed 2 tests; related checks passed 29 tests / 171 subtests with 2 opt-in
experiments skipped. The development database has not applied this migration.
See the [verification](docs/verification/phase-3-order-list-index.md) and
[study guide](docs/learning/phase-3-order-list-index-study-guide.md).
The old baseline experiment now rejects an already-indexed schema; reproduce
its historical measurements from e85e592 in an isolated checkout. That stage
handed off to Lua admission; COUNT and deep OFFSET costs remain.

Historical order-query baseline (2026-10-09): an opt-in disposable-MySQL experiment
captures the complete order/count/item SQL, existing indexes, execution plans,
ORM/serialization/render timings and response bytes. With 5,000 synthetic
orders per customer, a temporary `(user_id, created_at, id)` index removes the
first-page order sort and reduces that iterator's actual output from 5,000 to
20 rows; COUNT still reads 5,000 and page 201 still traverses 4,020 orders and
address lookups. The candidate was removed and the test database destroyed;
no application model, migration or development database index changed.
The experiment passed, and related checks passed 16 tests / 9 subtests with
the experiment skipped by default. See the [verification](docs/verification/phase-3-order-query-baseline.md)
and [study guide](docs/learning/phase-3-order-query-baseline-study-guide.md).
These are local warm synthetic measurements, not production capacity or
HTTP/network latency. Formal index adoption was the proposed next slice at that stage.

Current order-list status (2026-10-08): `GET /api/orders/?page=2` now returns
`count`, `next`, `previous`, and `results`, with a fixed page size of 20 and
ownership filtering before pagination. The customer page supports navigation,
refresh with the page retained in the URL, and retry/error recovery. Order
detail and checkout responses are unchanged. Isolated MySQL targets passed
28 tests / 171 subtests, related regression passed 63 tests / 8 subtests, and
the frontend build passed. Browser checks use mocked responses; no query-plan
or performance improvement is claimed. See the [pagination verification](docs/verification/phase-3-order-list-pagination.md)
and [complete study guide](docs/learning/phase-3-order-list-pagination-study-guide.md).
The owner has confirmed future Lua admission and Elasticsearch search;
neither is implemented by this slice. Older paragraphs retain their historical status.

Current flash-sale status (2026-10-08): Phase 6D adds public current-item discovery
and Session/CSRF checkout endpoints, reusing the Phase 6C-5 conditional UPDATE
and complete checkout transaction. The owner chose a temporary five-second
SESSION row-lock wait around the HTTP service, with restoration of the actual
original value after commit or rollback. Inventory UPDATE 1205/1213 returns
stock_busy/409; other checkout SQL 1205/1213 returns checkout_busy/409 after
the transaction exits. Unknown database errors propagate; there is no automatic
retry or whole-request deadline. Restoration failure discards the connection
and preserves the committed order result or original exception.
Real isolated inventory and user locks each produced MySQL 1205, full rollback,
restoration and original-key retry/replay. Cleanup and deadlock/disconnect
branches use fault injection, not real network/deadlock evidence.
Targets: 21 passed / 259 subtests; related regression: 216 passed / 662 subtests,
7 explicit experiments skipped and 12 RabbitMQ tests deselected. Frontend and
end-to-end acceptance remain pending; development data and migrations were not
changed. See the [HTTP guide](docs/learning/phase-6d-flash-sale-http-study-guide.md)
and [current wait evidence](docs/verification/phase-6d2-flash-sale-http-wait.md).
The [first HTTP record](docs/verification/phase-6d-flash-sale-http.md) preserves
the earlier candidate state before the owner answered the wait question.
The following paragraphs retain the state observed in each earlier slice.

The narrow business loop has
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
Phase 6C-1 adds the current NOWAIT service baseline for two different customers
competing for one item, including held-first commit/rollback and original-key
retry. After correcting the test observation context, 3 targets and all 23
promotion tests passed. That slice kept production code and migrations unchanged;
the following slice adds the complete conditional-update correctness comparison. See its
[verification](docs/verification/phase-6c1-flash-sale-contention.md) and
[learning guide](docs/learning/phase-6c1-flash-sale-contention-study-guide.md).
Phase 6C-2 extracts the unchanged default NOWAIT reservation into a private helper.
A test-only conditional UPDATE uses that same complete checkout transaction;
controlled commit/rollback, qualification and write-failure cases passed 6 targets
and all 29 promotion tests. Server PROCESSLIST recorded the unfinished inventory
UPDATE before release and the later 0/1 affected rows. Production still defaults
to NOWAIT; later slices measure resource costs before a final strategy ADR.
See the [comparison evidence](docs/verification/phase-6c2-flash-sale-inventory-comparison.md)
and [guide](docs/learning/phase-6c2-flash-sale-inventory-comparison-study-guide.md).
On 2026-10-08, Phase 6C-3 measured 24 finite batches (8 workers, 24 customer
journeys, two stock levels and retry budgets, three repetitions): all valid,
576 journeys and 767 attempts. A standard-library CLI independently reconstructs
latency and connection intervals from raw clocks. The target passed; ordinary
promotion regression was 29 passed and 1 explicitly skipped load experiment.
This is service-level cost evidence, not sustained production capacity; default
NOWAIT remains unchanged. See the [load evidence](docs/verification/phase-6c3-flash-sale-inventory-load.md)
and [guide](docs/learning/phase-6c3-flash-sale-inventory-load-study-guide.md).
Phase 6C-4 records the owner's accepted conditional UPDATE direction for one
flash-sale SKU and quantity one in [ADR 0001](docs/adr/0001-flash-sale-inventory-conditional-update.md).
Implementation is pending: production remains NOWAIT; this documentation slice
does not rerun experiments or change ordinary multi-SKU checkout. Bounded failure
handling and minimal production replacement are next, followed by HTTP and a
small customer entry. See the [decision learning guide](docs/learning/phase-6c4-flash-sale-strategy-decision-study-guide.md).
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
