# `python-cli` — Click + rich + pytest

## Stack
- Python 3.10+
- Click 8 (command parsing, subcommands)
- rich 13 (colored output, progress bars when needed)
- python-dotenv (env loading)
- pytest 8 (test runner, comes with Click's `CliRunner`)

## Generated layout
```
<name>/
├── pyproject.toml          # package metadata, declares the `<name>` script
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── .gitignore
├── .env.example
├── <name_pkg>/
│   ├── __init__.py
│   └── cli.py              # Click entry point with one `hello` command
└── tests/
    └── test_cli.py         # 2 passing tests (CliRunner)
```

## Steps the runner executes
1. Render templates (instant)
2. `python3 -m venv .venv` (~1.5 s)
3. `.venv/bin/pip install -r requirements.txt` (~30 s cold, ~1 s warm)
4. `.venv/bin/pip install -r requirements-dev.txt`
5. `.venv/bin/python -m pytest -q` (~0.1 s, sanity check)
6. `git init` + initial commit
7. (optional) GitHub repo create + push

## Total time
- Live smoke: **9.6 s** end-to-end on a warm box (`skip_github=true`).

## Required dependencies
None beyond Python 3.10+. No Node, no Docker, no external services.

## Next steps after scaffold
- Replace the placeholder `hello` command in `<name_pkg>/cli.py` with whatever the CLI does.
- Add subcommands as `@cli.command()` functions.
- For long ops use `rich.progress` to keep output skimmable.
