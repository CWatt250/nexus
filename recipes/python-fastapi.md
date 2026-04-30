# `python-fastapi` — FastAPI + SQLAlchemy + Alembic + Pydantic v2

## Stack
- Python 3.10+
- FastAPI 0.110+ (ASGI web framework)
- uvicorn (ASGI server)
- SQLAlchemy 2.0 (Core + ORM)
- Alembic 1.13 (migrations)
- Pydantic v2 (request/response shapes)
- pytest + httpx for tests

## Generated layout
```
<name>/
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── .env.example            # DATABASE_URL placeholder
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/.gitkeep
├── <name_pkg>/
│   ├── main.py             # FastAPI app, mounts routers
│   ├── db.py               # SQLAlchemy engine + session
│   ├── models.py           # Item placeholder model
│   ├── schemas.py          # Pydantic v2 ItemIn / ItemOut
│   └── routers/
│       ├── __init__.py
│       └── health.py       # GET /health → {ok: true}
└── tests/
    └── test_health.py      # TestClient-based sanity test
```

## Required dependencies
- Python 3.10+
- For Postgres: `psql` client + a running Postgres instance. The default
  `DATABASE_URL` in `.env.example` is `sqlite:///./app.db` so dev works
  out of the box.

## Next steps
- Drop a real model in `<name_pkg>/models.py` and run
  `alembic revision -m "init" --autogenerate && alembic upgrade head`.
- Add routers under `<name_pkg>/routers/<resource>.py` and register them in `main.py`.
- For background work, look at FastAPI BackgroundTasks or wire a Celery/Arq worker.
