# Project One — Django E-commerce

Project One is a backend-heavy Django/DRF modular monolith for building and
demonstrating reproducible engineering evidence. The current foundation creates
only infrastructure and the confirmed application boundaries. Business models
will be added through vertical slices.

## Foundation layout

- `backend/config`: Django settings, root URLs, and health check.
- `backend/apps/accounts`: the custom user model required before migration.
- `backend/apps/catalog`, `inventory`, `ordering`, and `payments`: empty domain
  boundaries reserved for later vertical slices.
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
