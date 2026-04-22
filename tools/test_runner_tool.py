"""Auto-detecting test runner for Nexus.

Detects the framework from repo contents (pytest, jest via package.json,
cargo, go, generic `npm test`) and runs it. Returns a structured summary
so the coding agent can tell pass from fail."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

_PYTHON = sys.executable or "python3"
_PYTEST = f"{_PYTHON} -m pytest -q"

TEST_TIMEOUT = 300
log = logging.getLogger("nexus.tests")


@dataclass
class TestResult:
    command: str
    returncode: int
    passed: int
    failed: int
    passed_all: bool
    failing: list[str]
    stdout: str
    stderr: str


def _resolve(repo_path: str) -> Path:
    return Path(repo_path).expanduser().resolve()


def detect_command(repo: Path) -> Optional[str]:
    """Pick a reasonable test command for the repo, or None if unknown."""
    if (repo / "pytest.ini").exists() or _has_pytest_cfg(repo):
        return _PYTEST
    # Python with tests/ dir + requirements
    if _is_python(repo) and (repo / "tests").exists():
        return _PYTEST
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {}) or {}
        except Exception:
            scripts = {}
        if "test" in scripts:
            return "npm test --silent"
        if (repo / "node_modules" / ".bin" / "jest").exists() or "jest" in str(scripts):
            return "npx jest --silent"
        if (repo / "node_modules" / ".bin" / "vitest").exists() or "vitest" in str(scripts):
            return "npx vitest run"
    if (repo / "Cargo.toml").exists():
        return "cargo test --quiet"
    if (repo / "go.mod").exists():
        return "go test ./..."
    # Python with any *_test.py or test_*.py files anywhere
    if _is_python(repo) and _has_any_test_files(repo):
        return _PYTEST
    return None


def _is_python(repo: Path) -> bool:
    if (repo / "pyproject.toml").exists() or (repo / "requirements.txt").exists():
        return True
    if any(repo.glob("*.py")):
        return True
    src = repo / "src"
    if src.exists() and any(src.rglob("*.py")):
        return True
    return False


def _has_pytest_cfg(repo: Path) -> bool:
    pp = repo / "pyproject.toml"
    if not pp.exists():
        return False
    try:
        return "[tool.pytest" in pp.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _has_any_test_files(repo: Path) -> bool:
    for pattern in ("test_*.py", "*_test.py"):
        if any(repo.rglob(pattern)):
            return True
    return False


def _parse_pytest(output: str) -> tuple[int, int, list[str]]:
    passed = failed = 0
    failing: list[str] = []
    m = re.search(r"(\d+)\s+passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", output)
    if m:
        failed = int(m.group(1))
    for line in output.splitlines():
        # pytest "short summary info": "FAILED tests/test_x.py::test_y - AssertionError: …"
        if line.startswith("FAILED "):
            failing.append(line[len("FAILED "):].strip())
    return passed, failed, failing


def _parse_jest(output: str) -> tuple[int, int, list[str]]:
    passed = failed = 0
    m = re.search(r"Tests:\s+(\d+)\s+failed,\s+(\d+)\s+passed", output)
    if m:
        failed, passed = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"Tests:\s+(\d+)\s+passed", output)
        if m:
            passed = int(m.group(1))
    failing = [ln.strip()[len("✕ "):] for ln in output.splitlines() if ln.strip().startswith("✕ ")]
    return passed, failed, failing


def _parse(output: str, cmd: str) -> tuple[int, int, list[str]]:
    if "pytest" in cmd:
        return _parse_pytest(output)
    if "jest" in cmd or "vitest" in cmd or "npm test" in cmd:
        return _parse_jest(output)
    # Generic: any non-zero exit with "fail" in output suggests failure;
    # otherwise assume all passed on rc=0.
    failed = len(re.findall(r"\bFAIL\b", output))
    return 0, failed, []


def _run(cmd: str, cwd: Path, timeout: int = TEST_TIMEOUT) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as exc:
        return 1, exc.stdout or "", (exc.stderr or "") + f"\n[timed out after {timeout}s]"


def run_tests_raw(repo_path: str, test_command: Optional[str] = None) -> TestResult:
    """Run tests and return the full structured result (for internal use)."""
    repo = _resolve(repo_path)
    cmd = test_command or detect_command(repo)
    if not cmd:
        return TestResult(
            command="(unknown)", returncode=-1, passed=0, failed=0, passed_all=False,
            failing=[], stdout="", stderr="Could not detect a test framework",
        )
    log.info("running tests in %s: %s", repo, cmd)
    rc, out, err = _run(cmd, repo)
    passed, failed, failing = _parse(out + "\n" + err, cmd)
    passed_all = rc == 0 and failed == 0
    return TestResult(
        command=cmd, returncode=rc, passed=passed, failed=failed,
        passed_all=passed_all, failing=failing, stdout=out, stderr=err,
    )


@tool
def run_tests(repo_path: str, test_command: Optional[str] = None) -> str:
    """Auto-detect the test framework for a repo and run its tests.

    Args:
      repo_path: path to the repo root.
      test_command: optional override (e.g. "pytest tests/test_foo.py -q").
    Returns a summary with command, pass/fail counts, failing test names, and
    trimmed stdout/stderr."""
    r = run_tests_raw(repo_path, test_command)
    head = (
        f"command: {r.command}\n"
        f"returncode: {r.returncode}\n"
        f"passed: {r.passed}  failed: {r.failed}  all_passed: {r.passed_all}\n"
    )
    if r.failing:
        head += "failing:\n  " + "\n  ".join(r.failing) + "\n"
    tail_out = (r.stdout or "").strip()
    tail_err = (r.stderr or "").strip()
    if len(tail_out) > 4000:
        tail_out = tail_out[:4000] + "\n[...trimmed...]"
    if len(tail_err) > 2000:
        tail_err = tail_err[:2000] + "\n[...trimmed...]"
    parts = [head.rstrip()]
    if tail_out:
        parts.append("stdout:\n" + tail_out)
    if tail_err:
        parts.append("stderr:\n" + tail_err)
    return "\n\n".join(parts)


@tool
def run_specific_test(repo_path: str, test_name: str) -> str:
    """Run a single test by name. Builds a pytest / jest command matching
    the detected framework and appends the test selector."""
    repo = _resolve(repo_path)
    base = detect_command(repo) or _PYTEST
    if "pytest" in base:
        cmd = f"{base} -k {test_name!r}"
    elif "jest" in base or "vitest" in base:
        cmd = f"{base} -t {test_name!r}"
    else:
        cmd = f"{base} {test_name}"
    return run_tests.invoke({"repo_path": repo_path, "test_command": cmd})


@tool
def watch_tests(repo_path: str, max_seconds: int = 60) -> str:
    """Run tests in watch mode for up to `max_seconds` seconds. Useful for
    TDD loops. Streams output into a single text blob."""
    repo = _resolve(repo_path)
    base = detect_command(repo)
    if not base:
        return "ERROR: no test framework detected"
    if "pytest" in base:
        cmd = f"{_PYTHON} -m pytest -q"
    elif "jest" in base:
        cmd = "npx jest --watch"
    elif "vitest" in base:
        cmd = "npx vitest --watch"
    else:
        cmd = base
    rc, out, err = _run(cmd, repo, timeout=max_seconds)
    return f"command: {cmd}\nreturncode: {rc}\n\nstdout:\n{out}\n\nstderr:\n{err}"


TEST_RUNNER_TOOLS = [run_tests, run_specific_test, watch_tests]
