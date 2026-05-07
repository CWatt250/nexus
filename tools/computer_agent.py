"""Phase 36 — Computer Use agent (Anthropic Computer Use API).

Drives a real browser on the Xvfb :99 display via Anthropic's native
computer_20250124 tool. The model sees screenshots and emits mouse /
keyboard actions; this module executes them locally with pyautogui +
xdotool, takes a follow-up screenshot, and returns it as a tool_result.

The agent loop:
    1. Build messages = [system, user_task, last_screenshot]
    2. client.beta.messages.create(model, tools=[computer], messages, betas)
    3. For every tool_use block: run the action, snapshot, append result
    4. stop_reason == "end_turn" → done
    5. Hard cap: 30 min wall-clock or $5 spend (configurable)

Safety stops abort the loop and emit a HALT marker when the model is
about to take a risky action (delete / send / billing / transfer /
production-env edit). Override only by re-running with `unsafe=True`.

Per-iteration screenshots are saved to ~/AI_Agent/cu_logs/<task_id>/
alongside a JSON transcript of the conversation.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path.home() / "AI_Agent"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import secrets  # noqa: E402

log = logging.getLogger("nexus.computer_agent")

CU_LOGS = ROOT / "cu_logs"
CU_LOGS.mkdir(parents=True, exist_ok=True)

DISPLAY = ":99"
SCREEN_W, SCREEN_H = 1920, 1080
DISPLAY_NUMBER = 99

MODEL = "claude-sonnet-4-6"
BETA_HEADER = "computer-use-2025-01-24"
COMPUTER_TOOL_TYPE = "computer_20250124"

# Computer Use pricing (USD per million tokens) — claude-sonnet-4-6.
# Adjust if Anthropic publishes new rates; conservative estimate keeps
# the per-task ceiling honest.
PRICE_INPUT_PER_M = 3.00
PRICE_OUTPUT_PER_M = 15.00

# Loop guardrails
MAX_WALL_SECONDS = 30 * 60
MAX_ITERATIONS = 200

# Safety — patterns in tool_use input.text that halt the loop.
DESTRUCTIVE_PATTERNS = [
    re.compile(r"\b(delete|drop|destroy|remove|terminate|erase|wipe)\b", re.I),
    re.compile(r"\b(transfer ownership|change owner|delete project|delete repo)\b", re.I),
    re.compile(r"\b(send|publish|post)\b.*\b(email|message|tweet|invoice)\b", re.I),
]

# Sensitive substrings — checked against the active window title.
# Browsers populate the title with the page title (and sometimes the URL
# host), so include both URL-path forms and bare keywords.
SENSITIVE_URL_HINTS = [
    # URL paths (when title contains the URL)
    "/billing", "/payment", "/subscription", "/checkout",
    "/api/keys", "/settings/api", "/team/transfer",
    "stripe.com/dashboard",
    # Bare title keywords
    "billing", "payment", "checkout", "invoice",
    "danger zone", "transfer ownership", "delete project",
    "delete repo", "service role", "api keys", "api settings",
    "settings/api",
]


@dataclass
class AgentResult:
    task_id: str
    status: str                       # "completed" | "halted" | "timeout" | "error"
    reason: str
    iterations: int
    elapsed_seconds: float
    cost_usd: float
    input_tokens: int
    output_tokens: int
    log_dir: Path
    final_screenshot: Optional[Path] = None
    halt_reason: Optional[str] = None
    transcript: list = field(default_factory=list)


def _ensure_display() -> None:
    """Verify Xvfb :99 is reachable. The pyautogui import below reads
    DISPLAY at module-load time, so we set it before importing."""
    os.environ.setdefault("DISPLAY", DISPLAY)
    try:
        proc = subprocess.run(
            ["xdpyinfo", "-display", DISPLAY],
            capture_output=True, timeout=2,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Xvfb not reachable at {DISPLAY}. "
                "Run `sudo systemctl start nexus-xvfb`."
            )
    except FileNotFoundError as exc:
        raise RuntimeError(f"xdpyinfo missing: {exc}") from exc


def _screenshot_b64() -> tuple[str, bytes]:
    """Take a PNG screenshot of :99 via scrot and return (base64, raw_bytes).

    Subprocess-only on purpose — pyautogui pulls in mouseinfo which
    sys.exit()s without python3-tk installed, breaking the import for
    the whole tool. scrot is a single binary with no Python deps.
    """
    import tempfile  # noqa: PLC0415
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        proc = subprocess.run(
            ["scrot", "-o", path],
            env={**os.environ, "DISPLAY": DISPLAY},
            capture_output=True, timeout=10,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"scrot failed (rc={proc.returncode}): {proc.stderr.decode(errors='replace')[:200]}"
            )
        raw = Path(path).read_bytes()
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass
    return base64.b64encode(raw).decode("ascii"), raw


def _active_window_title() -> str:
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            env={**os.environ, "DISPLAY": DISPLAY},
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _is_risky(action: str, action_input: dict, window_title: str) -> Optional[str]:
    """Return a halt reason if the action looks destructive, else None."""
    text = (action_input.get("text") or "").strip()
    if action in ("type", "key") and text:
        for pat in DESTRUCTIVE_PATTERNS:
            if pat.search(text):
                return f"matched destructive pattern in {action!r}: {text!r}"
    title_lower = window_title.lower()
    for hint in SENSITIVE_URL_HINTS:
        if hint in title_lower:
            return f"window title hints sensitive page: {window_title!r} ({hint!r})"
    return None


_BUTTON_CODE = {"left": "1", "middle": "2", "right": "3"}
_SCROLL_BUTTON = {"up": "4", "down": "5", "left": "6", "right": "7"}


def _xdo(*args: str, timeout: int = 5) -> tuple[int, str]:
    """Run xdotool with DISPLAY=:99 set. Returns (returncode, stderr)."""
    proc = subprocess.run(
        ["xdotool", *args],
        env={**os.environ, "DISPLAY": DISPLAY},
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stderr.strip()


def _execute_action(action: str, ainput: dict) -> Optional[str]:
    """Execute one Anthropic Computer Use tool action via xdotool.
    Returns an error string on failure, None on success."""
    try:
        if action in ("screenshot", "cursor_position"):
            return None  # caller takes the follow-up snapshot
        if action == "wait":
            duration = float(ainput.get("duration", 1))
            time.sleep(min(duration, 10))
            return None
        if action == "type":
            text = ainput.get("text", "")
            rc, err = _xdo("type", "--delay", "20", "--", text, timeout=20)
            return None if rc == 0 else f"xdotool type failed: {err}"
        if action == "key":
            keys = ainput.get("text", "").strip()
            rc, err = _xdo("key", "--clearmodifiers", keys)
            return None if rc == 0 else f"xdotool key failed: {err}"
        if action == "hold_key":
            keys = ainput.get("text", "").strip()
            duration = float(ainput.get("duration", 1))
            _xdo("keydown", "--clearmodifiers", keys)
            time.sleep(min(duration, 5))
            _xdo("keyup", "--clearmodifiers", keys)
            return None
        if action == "mouse_move":
            x, y = ainput["coordinate"]
            rc, err = _xdo("mousemove", str(int(x)), str(int(y)))
            return None if rc == 0 else f"xdotool mousemove failed: {err}"
        if action in ("left_click", "right_click", "middle_click"):
            button = action.split("_", 1)[0]
            coord = ainput.get("coordinate")
            if coord:
                _xdo("mousemove", str(int(coord[0])), str(int(coord[1])))
            rc, err = _xdo("click", _BUTTON_CODE[button])
            return None if rc == 0 else f"xdotool click failed: {err}"
        if action in ("double_click", "triple_click"):
            repeats = "2" if action == "double_click" else "3"
            coord = ainput.get("coordinate")
            if coord:
                _xdo("mousemove", str(int(coord[0])), str(int(coord[1])))
            rc, err = _xdo("click", "--repeat", repeats, "--delay", "100", "1")
            return None if rc == 0 else f"xdotool double/triple click failed: {err}"
        if action == "left_click_drag":
            start = ainput.get("start_coordinate")
            end = ainput.get("coordinate")
            if start:
                _xdo("mousemove", str(int(start[0])), str(int(start[1])))
            _xdo("mousedown", "1")
            if end:
                _xdo("mousemove", str(int(end[0])), str(int(end[1])))
            _xdo("mouseup", "1")
            return None
        if action == "left_mouse_down":
            coord = ainput.get("coordinate")
            if coord:
                _xdo("mousemove", str(int(coord[0])), str(int(coord[1])))
            rc, err = _xdo("mousedown", "1")
            return None if rc == 0 else f"xdotool mousedown failed: {err}"
        if action == "left_mouse_up":
            coord = ainput.get("coordinate")
            if coord:
                _xdo("mousemove", str(int(coord[0])), str(int(coord[1])))
            rc, err = _xdo("mouseup", "1")
            return None if rc == 0 else f"xdotool mouseup failed: {err}"
        if action == "scroll":
            coord = ainput.get("coordinate")
            if coord:
                _xdo("mousemove", str(int(coord[0])), str(int(coord[1])))
            direction = ainput.get("scroll_direction", "down")
            amount = int(ainput.get("scroll_amount", 3))
            scroll_btn = _SCROLL_BUTTON.get(direction, "5")
            rc, err = _xdo("click", "--repeat", str(max(1, amount)), scroll_btn)
            return None if rc == 0 else f"xdotool scroll failed: {err}"
        return f"unknown action: {action!r}"
    except FileNotFoundError:
        return "xdotool not installed — run /tmp/sudo-phase-36.sh first"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _save_iteration_screenshot(log_dir: Path, iteration: int, raw: bytes) -> Path:
    path = log_dir / f"iter_{iteration:03d}.png"
    path.write_bytes(raw)
    return path


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * PRICE_INPUT_PER_M
        + output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M
    )


SYSTEM_PROMPT = """You are Nexus's Computer Use agent. You drive a real browser on a 1920x1080 virtual display to complete dashboard tasks for Colton (Supabase, Vercel, Stripe, GitHub, etc.).

Operating rules:
- Take a screenshot first to see the current state.
- Move deliberately. Verify each step worked before the next.
- If a login screen blocks you, STOP — say "needs manual login" and end your turn. The operator will log in once and re-run.
- DO NOT click buttons that delete data, send email/messages, change billing, transfer ownership, or modify production env vars without explicit authorization in the user task. If you're unsure, stop and ask.
- When the task is complete, summarize what you did and end your turn (no more tool calls).
- Be concise in your reasoning. The operator only sees screenshots and your final summary.
"""


def run_task(
    task: str,
    *,
    task_id: Optional[str] = None,
    max_cost_usd: float = 5.00,
    max_seconds: int = MAX_WALL_SECONDS,
    unsafe: bool = False,
    on_iteration: Optional[Callable[[int, Path, str], None]] = None,
    dry_run: bool = False,
) -> AgentResult:
    """Run the Computer Use loop until the task is done or a guard trips.

    Args:
        task: Plain-English description of what to do.
        task_id: Optional caller-supplied id; otherwise generated.
        max_cost_usd: Hard ceiling on Anthropic spend per task.
        max_seconds: Hard wall-clock ceiling (default 30 min).
        unsafe: Skip the destructive-action regex stops. Required for
                tasks that legitimately need to delete / send / etc.
        on_iteration: Callback(iter, screenshot_path, model_text) used by
                the Telegram wrapper for live updates.
        dry_run: If True, runs the API once with a dummy first screenshot
                but doesn't execute any actions or loop. Used for smoke
                testing without burning real credentials.
    """
    _ensure_display()

    task_id = task_id or f"cu_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    log_dir = CU_LOGS / task_id
    log_dir.mkdir(parents=True, exist_ok=True)

    api_key = secrets.get("ANTHROPIC_API_KEY")
    if not api_key:
        return AgentResult(
            task_id=task_id, status="error",
            reason="ANTHROPIC_API_KEY missing from secrets.yaml",
            iterations=0, elapsed_seconds=0.0, cost_usd=0.0,
            input_tokens=0, output_tokens=0, log_dir=log_dir,
        )

    from anthropic import Anthropic  # noqa: PLC0415
    client = Anthropic(api_key=api_key)

    # Initial screenshot — show the model the starting state.
    try:
        b64, raw = _screenshot_b64()
    except Exception as exc:
        return AgentResult(
            task_id=task_id, status="error",
            reason=f"initial screenshot failed: {type(exc).__name__}: {exc}",
            iterations=0, elapsed_seconds=0.0, cost_usd=0.0,
            input_tokens=0, output_tokens=0, log_dir=log_dir,
        )
    initial_path = _save_iteration_screenshot(log_dir, 0, raw)

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Task:\n{task}\n\nStarting screenshot attached."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            ],
        },
    ]

    tools = [
        {
            "type": COMPUTER_TOOL_TYPE,
            "name": "computer",
            "display_width_px": SCREEN_W,
            "display_height_px": SCREEN_H,
            "display_number": DISPLAY_NUMBER,
        },
    ]

    started = time.monotonic()
    iterations = 0
    input_tokens = 0
    output_tokens = 0
    transcript: list[dict] = [{"role": "user", "content": f"Task: {task}"}]
    final_screenshot = initial_path
    final_text = ""

    while True:
        iterations += 1
        if iterations > MAX_ITERATIONS:
            return AgentResult(
                task_id=task_id, status="timeout",
                reason=f"hit max iterations ({MAX_ITERATIONS})",
                iterations=iterations, elapsed_seconds=time.monotonic() - started,
                cost_usd=_calc_cost(input_tokens, output_tokens),
                input_tokens=input_tokens, output_tokens=output_tokens,
                log_dir=log_dir, final_screenshot=final_screenshot,
                transcript=transcript,
            )
        if time.monotonic() - started > max_seconds:
            return AgentResult(
                task_id=task_id, status="timeout",
                reason=f"hit wall-clock cap ({max_seconds}s)",
                iterations=iterations, elapsed_seconds=time.monotonic() - started,
                cost_usd=_calc_cost(input_tokens, output_tokens),
                input_tokens=input_tokens, output_tokens=output_tokens,
                log_dir=log_dir, final_screenshot=final_screenshot,
                transcript=transcript,
            )
        running_cost = _calc_cost(input_tokens, output_tokens)
        if running_cost >= max_cost_usd:
            return AgentResult(
                task_id=task_id, status="timeout",
                reason=f"hit cost cap (${running_cost:.2f} >= ${max_cost_usd:.2f})",
                iterations=iterations, elapsed_seconds=time.monotonic() - started,
                cost_usd=running_cost,
                input_tokens=input_tokens, output_tokens=output_tokens,
                log_dir=log_dir, final_screenshot=final_screenshot,
                transcript=transcript,
            )

        try:
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
                betas=[BETA_HEADER],
            )
        except Exception as exc:
            return AgentResult(
                task_id=task_id, status="error",
                reason=f"API call failed: {type(exc).__name__}: {exc}",
                iterations=iterations, elapsed_seconds=time.monotonic() - started,
                cost_usd=_calc_cost(input_tokens, output_tokens),
                input_tokens=input_tokens, output_tokens=output_tokens,
                log_dir=log_dir, final_screenshot=final_screenshot,
                transcript=transcript,
            )

        usage = getattr(response, "usage", None)
        if usage:
            input_tokens += getattr(usage, "input_tokens", 0) or 0
            output_tokens += getattr(usage, "output_tokens", 0) or 0

        # Append assistant message back into history (full content blocks).
        assistant_blocks = [_block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": assistant_blocks})

        text_chunks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        final_text = "\n".join(t for t in text_chunks if t).strip() or final_text
        transcript.append({"role": "assistant", "iteration": iterations, "text": final_text,
                           "stop_reason": response.stop_reason})

        if dry_run:
            return AgentResult(
                task_id=task_id, status="completed",
                reason="dry-run — single API call, no actions executed",
                iterations=iterations, elapsed_seconds=time.monotonic() - started,
                cost_usd=_calc_cost(input_tokens, output_tokens),
                input_tokens=input_tokens, output_tokens=output_tokens,
                log_dir=log_dir, final_screenshot=final_screenshot,
                transcript=transcript,
            )

        if response.stop_reason != "tool_use":
            # Model is done — write transcript and return.
            (log_dir / "transcript.json").write_text(
                json.dumps(transcript, indent=2, default=str)
            )
            return AgentResult(
                task_id=task_id, status="completed",
                reason=final_text or "(no final text)",
                iterations=iterations, elapsed_seconds=time.monotonic() - started,
                cost_usd=_calc_cost(input_tokens, output_tokens),
                input_tokens=input_tokens, output_tokens=output_tokens,
                log_dir=log_dir, final_screenshot=final_screenshot,
                transcript=transcript,
            )

        # Execute every tool_use block and gather results into a single
        # user message of tool_result blocks.
        tool_results: list[dict] = []
        halt_reason: Optional[str] = None
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            action = (block.input or {}).get("action", "")
            ainput = block.input or {}

            window_title = _active_window_title()
            if not unsafe:
                risky = _is_risky(action, ainput, window_title)
                if risky:
                    halt_reason = risky
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text",
                                     "text": f"BLOCKED by safety: {risky}. Operator must re-run with unsafe=True."}],
                        "is_error": True,
                    })
                    break

            err = _execute_action(action, ainput)
            try:
                b64, raw = _screenshot_b64()
                final_screenshot = _save_iteration_screenshot(log_dir, iterations, raw)
            except Exception as exc:
                err = err or f"screenshot after action failed: {exc}"
                b64 = None
                raw = b""

            content_blocks: list[dict] = []
            if err:
                content_blocks.append({"type": "text", "text": f"action {action!r} error: {err}"})
            if b64:
                content_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                })
            if not content_blocks:
                content_blocks.append({"type": "text", "text": f"action {action!r} produced no output"})

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content_blocks,
                **({"is_error": True} if err else {}),
            })

            transcript.append({
                "iteration": iterations, "action": action,
                "input": _scrub(ainput), "error": err,
                "window_title": window_title,
            })

            if on_iteration:
                try:
                    on_iteration(iterations, final_screenshot, final_text or f"action: {action}")
                except Exception:
                    log.exception("on_iteration callback raised")

        messages.append({"role": "user", "content": tool_results})

        if halt_reason:
            (log_dir / "transcript.json").write_text(
                json.dumps(transcript, indent=2, default=str)
            )
            (log_dir / "HALT.txt").write_text(halt_reason + "\n")
            return AgentResult(
                task_id=task_id, status="halted",
                reason="safety stop tripped — see HALT.txt",
                iterations=iterations, elapsed_seconds=time.monotonic() - started,
                cost_usd=_calc_cost(input_tokens, output_tokens),
                input_tokens=input_tokens, output_tokens=output_tokens,
                log_dir=log_dir, final_screenshot=final_screenshot,
                halt_reason=halt_reason, transcript=transcript,
            )


def _block_to_dict(block: Any) -> dict:
    """Convert an Anthropic SDK content block back to its serialized form."""
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return dict(block)


def _scrub(d: dict) -> dict:
    """Drop image base64 from transcripts to keep them readable."""
    out = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 500:
            out[k] = f"<{len(v)} chars elided>"
        else:
            out[k] = v
    return out


# CLI entry point — used by the Telegram /computer handler and by smoke tests.
def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run the Computer Use agent.")
    parser.add_argument("task", help="Plain-English task description")
    parser.add_argument("--unsafe", action="store_true", help="skip destructive-action stops")
    parser.add_argument("--dry-run", action="store_true", help="one API call, no actions")
    parser.add_argument("--max-cost", type=float, default=5.00)
    parser.add_argument("--max-seconds", type=int, default=MAX_WALL_SECONDS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    res = run_task(
        args.task,
        max_cost_usd=args.max_cost,
        max_seconds=args.max_seconds,
        unsafe=args.unsafe,
        dry_run=args.dry_run,
    )
    print(json.dumps({
        "task_id": res.task_id, "status": res.status, "reason": res.reason,
        "iterations": res.iterations, "elapsed_seconds": round(res.elapsed_seconds, 1),
        "cost_usd": round(res.cost_usd, 4),
        "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
        "log_dir": str(res.log_dir),
        "final_screenshot": str(res.final_screenshot) if res.final_screenshot else None,
        "halt_reason": res.halt_reason,
    }, indent=2))
    return 0 if res.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
