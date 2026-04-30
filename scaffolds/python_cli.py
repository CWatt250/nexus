"""python-cli recipe: Click + rich + pytest."""
from __future__ import annotations

from .base import Recipe, Step


def _templates(ctx: dict) -> dict[str, str]:
    name = ctx["name"]
    pkg = name.replace("-", "_")
    return {
        "pyproject.toml": _PYPROJECT.format(name=name, pkg=pkg),
        "requirements.txt": _REQUIREMENTS,
        "requirements-dev.txt": _REQUIREMENTS_DEV,
        f"{pkg}/__init__.py": f'"""{name} — Click CLI scaffolded by Nexus."""\n__version__ = "0.0.1"\n',
        f"{pkg}/cli.py": _CLI_PY.format(pkg=pkg),
        "tests/__init__.py": "",
        "tests/test_cli.py": _TEST_CLI.format(pkg=pkg),
        "README.md": _README.format(name=name, pkg=pkg),
        ".gitignore": _GITIGNORE,
        ".env.example": "# Add app secrets here. Loaded via python-dotenv.\n",
    }


def _extra_steps(ctx: dict) -> list[Step]:
    pkg = ctx["name"].replace("-", "_")
    return [
        Step(
            name="create_venv",
            command="python3 -m venv .venv",
            cwd=ctx["project_dir"], timeout_s=60,
            progress="Creating Python virtualenv",
        ),
        Step(
            name="install_runtime_deps",
            command=".venv/bin/pip install -q -r requirements.txt",
            cwd=ctx["project_dir"], timeout_s=120,
            progress="Installing Click + rich + python-dotenv",
            skip_if=lambda c: c["opts"].get("skip_install"),
        ),
        Step(
            name="install_dev_deps",
            command=".venv/bin/pip install -q -r requirements-dev.txt",
            cwd=ctx["project_dir"], timeout_s=120,
            progress="Installing pytest",
            skip_if=lambda c: c["opts"].get("skip_install"),
        ),
        Step(
            name="run_tests",
            command=".venv/bin/python -m pytest -q",
            cwd=ctx["project_dir"], timeout_s=60,
            progress="Running initial test suite",
            skip_if=lambda c: c["opts"].get("skip_install") or c["opts"].get("skip_tests"),
        ),
    ]


_PYPROJECT = '''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.0.1"
description = "Scaffolded by Nexus."
requires-python = ">=3.10"
dependencies = [
    "click>=8.1",
    "rich>=13",
    "python-dotenv>=1",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
{name} = "{pkg}.cli:cli"

[tool.setuptools.packages.find]
include = ["{pkg}*"]
'''

_REQUIREMENTS = """click>=8.1
rich>=13
python-dotenv>=1
"""

_REQUIREMENTS_DEV = """pytest>=8
"""

_CLI_PY = '''"""Entry-point CLI for {pkg}."""
from __future__ import annotations

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option()
def cli() -> None:
    """{pkg} — replace this docstring with what the tool actually does."""


@cli.command()
@click.argument("name", default="world")
def hello(name: str) -> None:
    """Say hello to NAME."""
    console.print(f"[bold cyan]hello {{name}}[/bold cyan]")


if __name__ == "__main__":
    cli()
'''

_TEST_CLI = '''from click.testing import CliRunner

from {pkg}.cli import cli


def test_hello_default() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["hello"])
    assert result.exit_code == 0
    assert "hello world" in result.output


def test_hello_named() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["hello", "Colton"])
    assert result.exit_code == 0
    assert "hello Colton" in result.output
'''

_README = """# {name}

Scaffolded by Nexus (`python-cli` recipe).

## Quickstart
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
{name} hello
pytest
```

## Layout
- `{pkg}/cli.py` — Click entry point
- `tests/` — pytest suite (one passing test out of the box)
- `pyproject.toml` — package metadata, declares the `{name}` script

## Next steps
- Replace the `hello` command with whatever this CLI actually does.
- Add subcommands as `@cli.command()` functions in `{pkg}/cli.py`.
- For long-running ops, use `rich.progress` to keep output readable.
"""

_GITIGNORE = """__pycache__/
*.py[cod]
.venv/
.env
.pytest_cache/
*.egg-info/
dist/
build/
"""


RECIPE = Recipe(
    name="python-cli",
    display="Python CLI (Click + rich + pytest)",
    description="A self-contained CLI scaffold with one passing test, "
                "venv, and the standard click+rich layout.",
    base_command=None,  # pure-Python recipe
    template_files=_templates,
    extra_steps=_extra_steps,
    notes="No external services. Lightweight smoke target.",
)
