# PSOP Backend Scaffold

This directory now keeps only the backend scaffold that is independent from the old business implementation.

## What remains

- FastAPI application factory and startup entrypoint
- Basic `/`, `/healthz`, `/api/v1/system`, and `/api/v1/system/health` endpoints
- Shared settings and logging utilities
- Python packaging and test configuration

## What was removed

- Old skill registry business modules
- Old run engine business modules
- Old repositories, ORM models, and route handlers tied to the previous implementation

## Local development

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --host 127.0.0.1 --port 8011 --app-dir . --reload
```

Or from the repo root:

```bash
scripts/dev/run-server.sh
```

## Tests

```bash
scripts/dev/test-server.sh
```
