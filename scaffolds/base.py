"""Recipe + Runner foundation for the scaffolding system.

`Recipe` is a small dataclass each recipe module fills in:
  - `name`, `display`, `description`
  - `base_command` (or None for pure-Python recipes)
  - `extra_npm_packages` / `extra_dev_npm_packages` (for Node recipes)
  - `extra_pip_packages` (for Python recipes)
  - `template_files`: callable returning {relpath: text} given the
    project context dict (so recipes can interpolate `name` etc.)
  - `extra_steps`: ordered list of `Step` objects to run after the
    base command / template write

`Runner` wraps subprocess execution with:
  - Per-step timeout (default 120 s)
  - Telegram heartbeat publish every 60 s during a long step
  - Stdout/stderr captured + truncated for the final report
  - Best-effort progress emoji prefix matching the spec

Both pieces stay synchronous because the agent worker calls them from
inside an async coroutine via run_in_executor — no async noise here.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("nexus.scaffolds")

DEFAULT_STEP_TIMEOUT_S = 120
HEARTBEAT_INTERVAL_S = 60
TELEGRAM_PREFIX = "🏗️  "


def _publish_progress(label: str) -> None:
    """Best-effort Telegram heartbeat. Silent if Telegram isn't wired
    or if the import fails — scaffolding should never block on
    notifications."""
    try:
        from tools.telegram_tool import telegram_notify  # noqa: PLC0415
        telegram_notify.invoke({"message": f"{TELEGRAM_PREFIX}{label}"})
    except Exception as exc:
        log.info("telegram heartbeat skipped: %s", exc)


@dataclass
class StepResult:
    name: str
    command: str
    returncode: int
    elapsed_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False
    skipped: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.skipped


@dataclass
class Step:
    """One executable step. Either a shell command string OR a Python
    callable that takes (project_dir, opts) and runs to completion.
    Pure-Python steps are timed but not heartbeated — they should be
    fast (<10s); long ones use the shell path."""
    name: str
    command: str | None = None       # shell command, run via /bin/sh -c
    python_call: Callable | None = None
    cwd: Path | str | None = None
    timeout_s: int = DEFAULT_STEP_TIMEOUT_S
    progress: str = ""               # Telegram heartbeat text (empty = no announce)
    skip_if: Callable[[dict], bool] | None = None


@dataclass
class Recipe:
    name: str                         # slug, e.g. "nextjs-landing"
    display: str                      # "Next.js 14 Landing Page"
    description: str
    base_dir: str = "~/Dev"           # parent dir for new projects
    base_command: list[str] | None = None     # ["npx", "-y", "create-next-app@14"]
    base_command_args: Callable[[Path, dict], list[str]] | None = None
    extra_npm_packages: list[str] = field(default_factory=list)
    extra_dev_npm_packages: list[str] = field(default_factory=list)
    extra_pip_packages: list[str] = field(default_factory=list)
    template_files: Callable[[dict], dict[str, str]] = field(
        default_factory=lambda: lambda ctx: {}
    )
    extra_steps: Callable[[dict], list[Step]] = field(
        default_factory=lambda: lambda ctx: []
    )
    requires_node_version: tuple[int, int] | None = None  # (major, minor)
    notes: str = ""

    def scaffold(self, project_dir: Path, opts: dict) -> dict:
        """Drive the full scaffold. Returns a result dict suitable for
        Telegram + run-log:
          {ok, project_dir, steps: [StepResult,...], summary, error}
        """
        return _scaffold_recipe(self, project_dir, opts)


# ---------------------------------------------------------------------------
# Runner internals
# ---------------------------------------------------------------------------


def _scaffold_recipe(recipe: Recipe, project_dir: Path, opts: dict) -> dict:
    """Execute every phase of a recipe. Single source of truth for the
    end-to-end pipeline so individual recipes stay declarative."""
    skip_github = bool(opts.get("skip_github"))
    skip_install = bool(opts.get("skip_install"))
    skip_dev_smoke = bool(opts.get("skip_dev_smoke"))
    ctx = {
        "name": project_dir.name,
        "project_dir": str(project_dir),
        "opts": opts,
        "recipe": recipe.name,
    }

    results: list[StepResult] = []
    project_dir.parent.mkdir(parents=True, exist_ok=True)

    if project_dir.exists() and any(project_dir.iterdir()):
        return {
            "ok": False,
            "project_dir": str(project_dir),
            "steps": [],
            "summary": "",
            "error": f"refusing to scaffold into non-empty directory: {project_dir}",
        }

    _publish_progress(f"Setting up {recipe.display}...")

    # Phase 1: base command (e.g. create-next-app) — only if defined.
    if recipe.base_command:
        argv = list(recipe.base_command)
        if recipe.base_command_args:
            argv += recipe.base_command_args(project_dir, opts)
        results.append(_run_step(Step(
            name="base_scaffold",
            command=" ".join(shlex.quote(a) for a in argv),
            cwd=project_dir.parent,
            timeout_s=180,
            progress="Running base scaffolder",
        ), ctx))
        if not results[-1].ok:
            return _summarize(recipe, project_dir, results, ctx)

    # Phase 2: ensure project_dir exists (pure-Python recipes don't get
    # one for free).
    project_dir.mkdir(parents=True, exist_ok=True)

    # Phase 3: template files.
    try:
        templates = recipe.template_files(ctx)
    except Exception as exc:
        results.append(StepResult(
            name="render_templates", command="(python)",
            returncode=1, elapsed_s=0.0,
            stderr_tail=f"{type(exc).__name__}: {exc}",
        ))
        return _summarize(recipe, project_dir, results, ctx)

    if templates:
        t0 = time.monotonic()
        written = 0
        try:
            for rel, content in templates.items():
                target = project_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                written += 1
        except OSError as exc:
            results.append(StepResult(
                name="render_templates", command="(python)",
                returncode=1, elapsed_s=time.monotonic() - t0,
                stderr_tail=f"{type(exc).__name__}: {exc}",
            ))
            return _summarize(recipe, project_dir, results, ctx)
        results.append(StepResult(
            name="render_templates", command=f"(wrote {written} files)",
            returncode=0, elapsed_s=time.monotonic() - t0,
        ))
        _publish_progress(f"Generated {written} template files")

    # Phase 4: extra npm/pip packages.
    npm = recipe.extra_npm_packages
    npm_dev = recipe.extra_dev_npm_packages
    if (npm or npm_dev) and not skip_install:
        if npm:
            results.append(_run_step(Step(
                name="npm_install_runtime",
                command="npm install " + " ".join(shlex.quote(p) for p in npm),
                cwd=project_dir, timeout_s=180,
                progress=f"Installing {len(npm)} runtime packages",
            ), ctx))
            if not results[-1].ok:
                return _summarize(recipe, project_dir, results, ctx)
        if npm_dev:
            results.append(_run_step(Step(
                name="npm_install_dev",
                command="npm install -D " + " ".join(shlex.quote(p) for p in npm_dev),
                cwd=project_dir, timeout_s=180,
                progress=f"Installing {len(npm_dev)} dev packages",
            ), ctx))
            if not results[-1].ok:
                return _summarize(recipe, project_dir, results, ctx)

    if recipe.extra_pip_packages and not skip_install:
        # Use the project's venv if one was created; fall back to the
        # nexus venv so the user has something working out of the box.
        proj_venv = project_dir / ".venv" / "bin" / "pip"
        pip_cmd = (
            str(proj_venv) if proj_venv.exists()
            else "/home/cwatt250/AI_Agent/venv/bin/pip"
        )
        cmd = f"{pip_cmd} install " + " ".join(
            shlex.quote(p) for p in recipe.extra_pip_packages
        )
        results.append(_run_step(Step(
            name="pip_install",
            command=cmd,
            cwd=project_dir, timeout_s=180,
            progress=f"Installing {len(recipe.extra_pip_packages)} Python packages",
        ), ctx))
        if not results[-1].ok:
            return _summarize(recipe, project_dir, results, ctx)

    # Phase 5: extra recipe-defined steps.
    try:
        extra = recipe.extra_steps(ctx)
    except Exception as exc:
        results.append(StepResult(
            name="resolve_extra_steps", command="(python)",
            returncode=1, elapsed_s=0.0,
            stderr_tail=f"{type(exc).__name__}: {exc}",
        ))
        return _summarize(recipe, project_dir, results, ctx)
    for step in extra:
        if step.skip_if and step.skip_if(ctx):
            results.append(StepResult(
                name=step.name, command=step.command or "(python)",
                returncode=0, elapsed_s=0.0, skipped=True,
                note="skipped by recipe",
            ))
            continue
        results.append(_run_step(step, ctx))
        if not results[-1].ok:
            return _summarize(recipe, project_dir, results, ctx)

    # Phase 6: git init + initial commit.
    results.append(_run_step(Step(
        name="git_init",
        command="git init -q -b main && git add -A "
                f"&& git -c user.name='nexus' -c user.email='nexus@wattbott.local' "
                f"commit -q -m 'chore: initial scaffold from {recipe.name}'",
        cwd=project_dir, timeout_s=60,
        progress="Initializing git",
    ), ctx))
    # Even if git fails (e.g. no commits because dir is empty) we keep going.

    # Phase 7: GitHub repo + push (skipped on smoke).
    if not skip_github:
        repo_url = _create_github_repo(project_dir.name, ctx)
        if repo_url:
            results.append(_run_step(Step(
                name="git_push",
                command=f"git remote add origin {shlex.quote(repo_url)} && "
                        "git push -q -u origin main",
                cwd=project_dir, timeout_s=60,
                progress="Pushing initial commit to GitHub",
            ), ctx))
            ctx["github_url"] = repo_url
        else:
            results.append(StepResult(
                name="github_create",
                command="github_create_repo",
                returncode=1, elapsed_s=0.0,
                note="skipped — github_create_repo unavailable or failed",
                skipped=True,
            ))

    # Phase 8: optional dev-server smoke for Node recipes.
    if recipe.base_command and not skip_dev_smoke and not skip_install:
        results.append(_run_dev_smoke(project_dir, ctx))

    return _summarize(recipe, project_dir, results, ctx)


def _run_step(step: Step, ctx: dict) -> StepResult:
    """Run one step with a heartbeat thread. Pure-python steps don't get
    a heartbeat — they're expected to finish fast."""
    if step.python_call is not None:
        t0 = time.monotonic()
        try:
            step.python_call(ctx)
        except Exception as exc:
            return StepResult(
                name=step.name, command="(python)",
                returncode=1, elapsed_s=time.monotonic() - t0,
                stderr_tail=f"{type(exc).__name__}: {exc}",
            )
        return StepResult(
            name=step.name, command="(python)",
            returncode=0, elapsed_s=time.monotonic() - t0,
        )

    if not step.command:
        return StepResult(
            name=step.name, command="",
            returncode=0, elapsed_s=0.0, skipped=True,
            note="no command",
        )

    if step.progress:
        _publish_progress(step.progress)

    cwd = Path(step.cwd) if step.cwd else None
    t0 = time.monotonic()
    stop_hb = threading.Event()

    def _heartbeat():
        # First beat fires at HEARTBEAT_INTERVAL_S, then every interval.
        while not stop_hb.wait(HEARTBEAT_INTERVAL_S):
            elapsed = time.monotonic() - t0
            _publish_progress(
                f"…still on '{step.name}' ({elapsed:.0f}s, timeout {step.timeout_s}s)"
            )

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()
    try:
        proc = subprocess.run(
            step.command,
            shell=True,
            cwd=str(cwd) if cwd else None,
            timeout=step.timeout_s,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        stop_hb.set()
        return StepResult(
            name=step.name, command=step.command,
            returncode=124, elapsed_s=time.monotonic() - t0,
            stdout_tail=(exc.stdout or b"").decode("utf-8", "replace")[-1500:] if isinstance(exc.stdout, bytes) else (exc.stdout or "")[-1500:],
            stderr_tail=(exc.stderr or b"").decode("utf-8", "replace")[-1500:] if isinstance(exc.stderr, bytes) else (exc.stderr or "")[-1500:],
            timed_out=True,
        )
    finally:
        stop_hb.set()
        hb.join(timeout=2)

    return StepResult(
        name=step.name, command=step.command,
        returncode=proc.returncode, elapsed_s=time.monotonic() - t0,
        stdout_tail=(proc.stdout or "")[-1500:],
        stderr_tail=(proc.stderr or "")[-1500:],
    )


def _create_github_repo(name: str, ctx: dict) -> str | None:
    """Use the existing github_create_repo tool. Returns the clone URL
    or None on any failure (we want to keep going, not abort scaffold)."""
    try:
        from tools.github_tool import github_create_repo  # noqa: PLC0415
        out = github_create_repo.invoke({
            "name": name, "private": True,
            "description": f"Nexus-scaffolded {ctx.get('recipe', '')}",
        })
        # The tool returns a string. Parse a clone URL out of it.
        for line in (out or "").splitlines():
            line = line.strip()
            if line.startswith("https://github.com/") and line.endswith(".git"):
                return line
            if line.startswith("git@github.com:"):
                return line
        # Fallback: construct from `name` if the tool succeeded but
        # didn't echo a URL we recognise.
        if "created" in (out or "").lower() or "ok" in (out or "").lower():
            return f"https://github.com/CWatt250/{name}.git"
    except Exception as exc:
        log.warning("github_create_repo failed: %s", exc)
    return None


def _run_dev_smoke(project_dir: Path, ctx: dict) -> StepResult:
    """For Node recipes: launch `npm run dev` in the background, wait
    until localhost:3000 returns 200, kill the process. Best-effort —
    a failed smoke shouldn't fail the whole scaffold."""
    import socket
    import urllib.request

    cmd = "npm run dev"
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=str(project_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
    except OSError as exc:
        return StepResult(
            name="dev_smoke", command=cmd,
            returncode=1, elapsed_s=time.monotonic() - t0,
            stderr_tail=str(exc), skipped=True,
        )
    deadline = time.monotonic() + 60
    ok = False
    last_err = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:3000/", timeout=3
            ) as resp:
                if 200 <= resp.status < 500:
                    ok = True
                    break
        except (urllib.error.URLError, socket.timeout, ConnectionRefusedError) as exc:
            last_err = str(exc)
        time.sleep(2)
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    return StepResult(
        name="dev_smoke", command=cmd,
        returncode=0 if ok else 1,
        elapsed_s=time.monotonic() - t0,
        stdout_tail=(proc.stdout.read() if proc.stdout else "")[-800:],
        note="dev server reached" if ok else f"dev server not reachable: {last_err}",
    )


def _summarize(recipe: Recipe, project_dir: Path, results: list[StepResult],
               ctx: dict) -> dict:
    ok = all(r.ok or r.skipped for r in results)
    failed = [r for r in results if not r.ok and not r.skipped]
    summary_lines = [
        f"Scaffold {recipe.display!r} → {project_dir}",
        f"Steps: {len(results)} ({'all green' if ok else f'{len(failed)} failed'})",
    ]
    for r in results:
        flag = "✓" if r.ok else ("…" if r.skipped else "✗")
        summary_lines.append(
            f"  {flag} {r.name:24s} {r.elapsed_s:6.1f}s "
            f"{(r.note or '')[:60]}"
        )
    if ctx.get("github_url"):
        summary_lines.append(f"GitHub: {ctx['github_url']}")
    summary = "\n".join(summary_lines)

    if ok:
        _publish_progress(
            f"✅ Project {project_dir.name} scaffolded. "
            f"Local: {project_dir}"
            + (f"  GitHub: {ctx['github_url']}" if ctx.get("github_url") else "")
        )
    else:
        _publish_progress(
            f"⚠️ Scaffold {project_dir.name} hit {len(failed)} failure(s). "
            f"First: {failed[0].name}"
        )

    return {
        "ok": ok,
        "project_dir": str(project_dir),
        "steps": [r.__dict__ for r in results],
        "summary": summary,
        "error": "" if ok else "; ".join(f.name for f in failed),
        "github_url": ctx.get("github_url", ""),
    }
