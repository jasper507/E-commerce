# Project One — Django E-commerce

Project One is a backend-heavy Django/DRF modular monolith for building and
demonstrating reproducible engineering evidence. Accounts Session/CSRF and the
public Catalog baseline are implemented. The Guest Catalog Browse Vue UI is
locally verified. Interview Core MVP-1 Authenticated Cart is on `main`.
MVP-2 Minimum Address is implemented, automatically verified, and committed on
the local `main`; it is awaiting learning review and has not been pushed.

## Foundation layout

- `backend/config`: Django settings, root URLs, and health check.
- `backend/apps/accounts`: custom user, Session/CSRF authentication, and owned
  Address models/rules/APIs.
- `backend/apps/catalog`, `inventory`: public Category/Product/SKU browse APIs
  and the baseline SKU inventory model.
- `backend/apps/ordering`: authenticated Cart/CartItem models, rules, and API;
  order models remain for a later slice.
- `backend/apps/payments`: boundary reserved for the payment slice.
- `frontend`: Vue 3 + TypeScript/Vite catalog, Session login, cart, and minimum
  address UI.
- `tests`: pytest/pytest-django tests that use an isolated MySQL database.
- `compose.yaml`: project-owned MySQL 8.4 service on `127.0.0.1:3307`.

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
.\.venv\Scripts\python.exe -m pytest backend\apps\catalog\tests\test_detail_cache.py -q -s
```

Each test owns a random key prefix and deletes only its own keys, never FLUSHDB.
Without an explicit test Redis URL these integration tests skip; an explicitly
configured but unreachable Redis causes a failure. Never point this setting at
an external or shared service. The failure experiment reserves an unused local
port; it does not shut down Redis or change its global settings.

See [the study guide](docs/learning/phase-4-product-detail-cache-study-guide.md)
for the flow and stale-data boundaries, and
[the verification record](docs/verification/phase-4-product-detail-cache.md)
for actual observations and the reproduced stale-refill limitation.

## Guest Catalog Browse UI

See `frontend/README.md` for local startup and
`docs/verification/guest-catalog-browse.md` for the observed verification results,
an isolated browser fixture, the end-to-end request flow, and learning outcomes.

The UI uses native `fetch`, Vue Router, and a development-only Vite `/api` proxy.
It now includes minimal Session login, authenticated cart operations, and owned
address management. It does not implement custom Vue Admin, checkout, order
address snapshots, inventory reservation, payment, search, or image upload. The
cart does not lock price or reserve stock.

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
  favorite, review, MQ, search, and third-party payment are not Interview Core
  blockers.
