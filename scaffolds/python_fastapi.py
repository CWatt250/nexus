"""python-fastapi recipe: FastAPI + SQLAlchemy + Alembic + Pydantic v2."""
from __future__ import annotations

from .base import Recipe, Step


def _templates(ctx: dict) -> dict[str, str]:
    name = ctx["name"]
    pkg = name.replace("-", "_")
    return {
        "pyproject.toml": _PYPROJECT.format(name=name, pkg=pkg),
        "requirements.txt": _REQUIREMENTS,
        "requirements-dev.txt": _REQUIREMENTS_DEV,
        f"{pkg}/__init__.py": f'__version__ = "0.0.1"\n',
        f"{pkg}/main.py": _MAIN.format(pkg=pkg),
        f"{pkg}/db.py": _DB,
        f"{pkg}/models.py": _MODELS,
        f"{pkg}/schemas.py": _SCHEMAS,
        f"{pkg}/routers/__init__.py": "",
        f"{pkg}/routers/health.py": _ROUTER_HEALTH,
        "tests/__init__.py": "",
        "tests/test_health.py": _TEST_HEALTH.format(pkg=pkg),
        "alembic.ini": _ALEMBIC_INI.format(pkg=pkg),
        "alembic/env.py": _ALEMBIC_ENV.format(pkg=pkg),
        "alembic/script.py.mako": _ALEMBIC_MAKO,
        "alembic/versions/.gitkeep": "",
        "README.md": _README.format(name=name, pkg=pkg),
        ".env.example": _ENV_EXAMPLE,
        ".gitignore": _GITIGNORE,
    }


def _extra_steps(ctx: dict) -> list[Step]:
    return [
        Step(
            name="create_venv",
            command="python3 -m venv .venv",
            cwd=ctx["project_dir"], timeout_s=60,
            progress="Creating Python venv",
        ),
        Step(
            name="install_runtime_deps",
            command=".venv/bin/pip install -q -r requirements.txt",
            cwd=ctx["project_dir"], timeout_s=180,
            progress="Installing FastAPI + SQLAlchemy + Alembic",
            skip_if=lambda c: c["opts"].get("skip_install"),
        ),
        Step(
            name="install_dev_deps",
            command=".venv/bin/pip install -q -r requirements-dev.txt",
            cwd=ctx["project_dir"], timeout_s=120,
            progress="Installing pytest + httpx",
            skip_if=lambda c: c["opts"].get("skip_install"),
        ),
    ]


_PYPROJECT = '''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.0.1"
requires-python = ">=3.10"

[tool.setuptools.packages.find]
include = ["{pkg}*"]
'''

_REQUIREMENTS = """fastapi>=0.110
uvicorn[standard]>=0.27
sqlalchemy>=2.0
alembic>=1.13
pydantic>=2.6
pydantic-settings>=2.1
python-dotenv>=1
"""

_REQUIREMENTS_DEV = """pytest>=8
httpx>=0.27
"""

_MAIN = '''"""FastAPI app entry point."""
from __future__ import annotations

from fastapi import FastAPI

from {pkg}.routers import health

app = FastAPI(title="{pkg}", version="0.0.1")
app.include_router(health.router, prefix="/health", tags=["health"])
'''

_DB = '''"""SQLAlchemy engine + session factory."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False, autoflush=False)
Base = declarative_base()
'''

_MODELS = '''"""SQLAlchemy models go here. Example placeholder."""
from sqlalchemy import Column, Integer, String

from .db import Base


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
'''

_SCHEMAS = '''"""Pydantic v2 request/response schemas."""
from pydantic import BaseModel


class ItemIn(BaseModel):
    name: str


class ItemOut(ItemIn):
    id: int

    class Config:
        from_attributes = True
'''

_ROUTER_HEALTH = '''"""Health endpoint."""
from fastapi import APIRouter

router = APIRouter()


@router.get("")
def healthz() -> dict:
    return {"ok": True}
'''

_TEST_HEALTH = '''from fastapi.testclient import TestClient

from {pkg}.main import app


def test_health() -> None:
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {{"ok": True}}
'''

_ALEMBIC_INI = '''[alembic]
script_location = alembic
sqlalchemy.url = sqlite:///./app.db

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
'''

_ALEMBIC_ENV = '''from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from {pkg}.db import Base
from {pkg} import models  # noqa: F401 — register models

config = context.config
fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

_ALEMBIC_MAKO = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''

_README = """# {name}

FastAPI scaffold by Nexus (`python-fastapi` recipe).

## Quickstart
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn {pkg}.main:app --reload
pytest
```

## Layout
- `{pkg}/main.py` — FastAPI app, mounts routers
- `{pkg}/db.py` — SQLAlchemy engine + session
- `{pkg}/models.py` — SQLAlchemy models
- `{pkg}/schemas.py` — Pydantic v2 request/response shapes
- `{pkg}/routers/` — one file per resource
- `alembic/` — migrations (`alembic revision -m "msg"` then `alembic upgrade head`)
"""

_ENV_EXAMPLE = """DATABASE_URL=sqlite:///./app.db
"""

_GITIGNORE = """__pycache__/
*.py[cod]
.venv/
.env
.pytest_cache/
*.egg-info/
*.db
"""


RECIPE = Recipe(
    name="python-fastapi",
    display="FastAPI + SQLAlchemy + Alembic + Pydantic v2",
    description="A REST scaffold ready for routers + Alembic migrations.",
    base_command=None,
    template_files=_templates,
    extra_steps=_extra_steps,
)
