"""Phase 3 — local screenshot → decide → act → verify loop on Nexus's :99 desktop.

One brain call per step (Ornith with its built-in vision projector, JSON
schema output, think=False, images downscaled to ≤1024 px). The model is
given the top a11y elements as text and the small screenshot; it prefers
element names, falling back to "x,y" in small-image space.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

ROOT = Path.home() / "AI_Agent"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.brain import get_brain_model, num_ctx_for, think_param  # noqa: E402
from tools import desktop  # noqa: E402

log = logging.getLogger("nexus.desktop_agent")

ACTIONS = ["click", "double_click", "type", "press", "scroll", "open_url",
           "focus", "wait", "done"]

# Property order matters: Ollama's schema grammar emits fields in this
# order, so the model commits to task_complete BEFORE picking an action.
SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "task_complete": {"type": "boolean"},
        "action": {"type": "string", "enum": ACTIONS},
        "target": {"type": "string"},
        "text": {"type": "string"},
        "done_summary": {"type": "string"},
    },
    "required": ["thought", "task_complete", "action", "target", "text", "done_summary"],
}

_LEAK_RE = re.compile(r"<\|?/?(tool_call|invoke|function|im_end|im_start|think)[^>]*>?.*$", re.S | re.I)


def _clean(s) -> str:
    """Strip leaked tool-call / chat-template tokens from a model field."""
    return _LEAK_RE.sub("", str(s or "")).strip()

# Image long edge for the per-step screenshot. Prompt-eval (CPU CLIP
# projector) scales with pixels — measured 2026-09-12 on Ornith-1.5:
# 1024px = 9.8 s, 768px = 4.4 s, 640px = 2.4 s per NEW image. 768 keeps a
# step under ~5 s; the a11y list carries the precision.
STEP_IMG_EDGE = int(os.environ.get("NEXUS_DESKTOP_IMG_EDGE", "768"))

SENSITIVE_RE = re.compile(r"password|passwd|billing|checkout|card|ssn|cvv|social security", re.I)

SYSTEM = """You drive a Linux desktop (Chrome browser) one action at a time to complete the user's task.
Each turn you get: the task, the active window title, a numbered list of visible UI elements from the accessibility tree, the result of your previous action, and a screenshot.
Reply with ONE JSON object: {"thought": "...", "task_complete": true|false, "action": "...", "target": "...", "text": "...", "done_summary": "..."}
task_complete: true only when the current screen already shows the task's goal state — then action is "done" and done_summary states the outcome/answer.
Actions:
- click / double_click: target = an element NAME copied exactly from the ELEMENTS list (preferred) or "x,y" pixel coords in the screenshot. Never leave target empty.
- type: text = what to type (target = optional element name to click first). Use press with "enter" to submit.
- press: text = key or combo, e.g. "enter", "tab", "ctrl+l", "escape", "pagedown".
- scroll: text = "down" or "up" (optional number of clicks, e.g. "down 5").
- open_url: text = full URL. Fastest way to navigate — use it instead of typing in the address bar.
- focus: target = window title substring.
- wait: page still loading.
- done: task complete — put the outcome / any answer in done_summary.
Rules: one action per turn. Prefer element names from the list over coordinates — copy the name exactly. If the previous action failed, try a different approach. Never guess credentials. If your thought concludes the task is already satisfied by the current screen, the action MUST be "done"."""


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _elements_text(els: list[dict], limit: int = 60) -> str:
    lines = []
    for i, el in enumerate(els[:limit]):
        sx, sy = desktop.to_small(el["x"] + el["w"] // 2, el["y"] + el["h"] // 2)
        lines.append(f"{i + 1}. {el['role']}: \"{el['name']}\" @{sx},{sy}")
    return "\n".join(lines) if lines else "(accessibility tree unavailable — use the screenshot and x,y coords)"


def _brain_step(task: str, window: str, els_text: str, last_result: str,
                small_jpg: str, history: list[str], model: str, timeout: float = 120) -> dict:
    import ollama  # noqa: PLC0415
    sw, sh = desktop.LAST_SHOT.get("small_size", (1024, 576))
    user = (f"TASK: {task}\n"
            f"ACTIVE WINDOW: {window or '(none)'}\n"
            f"SCREENSHOT SIZE: {sw}x{sh} (x,y coords are in this space)\n"
            f"PREVIOUS ACTIONS: {'; '.join(history[-6:]) or '(none)'}\n"
            f"LAST RESULT: {last_result or '(none)'}\n"
            f"ELEMENTS:\n{els_text}\n\nNext action as JSON.")
    client = ollama.Client(host="http://localhost:11434", timeout=timeout)
    resp = client.chat(
        model=model,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": user, "images": [_b64(small_jpg)]}],
        stream=False, keep_alive=-1, think=think_param(model), format=SCHEMA,
        options={"temperature": 0.1, "num_predict": 300, "num_ctx": num_ctx_for(model)},
    )
    content = ((resp.get("message") or {}).get("content") or "").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {}
    step = {k: _clean(data.get(k)) for k in ("thought", "action", "target", "text", "done_summary")}
    if data.get("task_complete") is True:
        step["action"] = "done"
        step["done_summary"] = step["done_summary"] or step["thought"]
    return step


_COORD_RE = re.compile(r"^\s*\(?\s*(-?\d+)\s*[, ]\s*(-?\d+)\s*\)?\s*$")


def _resolve_target(target: str, els: list[dict]) -> tuple[Optional[tuple[int, int]], str]:
    """→ ((x, y) full-res, how) — element name via a11y, 'x,y' in small space,
    else a vision locate call on the last small screenshot."""
    m = _COORD_RE.match(target or "")
    if m:
        return desktop.to_full(int(m.group(1)), int(m.group(2))), "coords"
    if els:
        el = desktop.find_element(target, els)
        if el:
            return (el["cx"], el["cy"]), f"a11y:{el['name'][:40]!r}"
        # An element list exists and the name isn't in it: fail fast so the
        # model re-reads the list. The vision guess (measured ~150px off,
        # same point for any query) is worse than a clean miss.
        return None, "not in element list"
    # vision fallback — only when there is no element source at all
    try:
        from tools.computer_use_tool import locate_on_small  # noqa: PLC0415
        pt = locate_on_small(target, desktop.LAST_SHOT.get("small"))
        if pt:
            return desktop.to_full(*pt), "vision"
    except Exception as exc:  # noqa: BLE001
        log.info("vision locate failed: %s", exc)
    return None, "not found"


def _execute(step: dict, els: list[dict], approved: bool) -> tuple[str, Optional[str]]:
    """Run one action. Returns (result_text, needs_confirm_reason|None)."""
    act, target, text = step["action"], (step.get("target") or "").strip(), step.get("text") or ""
    if act in ("click", "double_click"):
        if not target:
            return f"{act} failed: empty target — name an element from the list or give x,y", None
        pt, how = _resolve_target(target, els)
        if not pt:
            return f"{act} failed: could not locate {target!r}", None
        fn = desktop.double_click if act == "double_click" else desktop.click
        return f"{fn(*pt)} [{how}]", None
    if act == "type":
        win = desktop.active_window_title()
        field = target
        if not approved and (SENSITIVE_RE.search(win) or SENSITIVE_RE.search(field)):
            return "blocked: sensitive field", f"typing into {field or win!r} (matches password/billing/checkout/card/ssn)"
        if target:
            pt, how = _resolve_target(target, els)
            if pt:
                desktop.click(*pt)
                time.sleep(0.2)
        return desktop.type_text(text), None
    if act == "press":
        return desktop.press(text or target), None
    if act == "scroll":
        spec = (text or target or "down").lower()
        n = int(m.group(1)) if (m := re.search(r"(\d+)", spec)) else 5
        return desktop.scroll(dy=-n if "up" in spec else n), None
    if act == "open_url":
        return desktop.open_url(text or target), None
    if act == "focus":
        return desktop.focus(target or text), None
    if act == "wait":
        time.sleep(1.5)
        return "waited 1.5s", None
    return f"unknown action {act!r}", None


def run_desktop_task(task: str, max_steps: int = 15, progress_cb: Optional[Callable] = None,
                     watch: bool = False, approved: bool = False, model: str | None = None) -> dict:
    """Screenshot → a11y + vision brain call → act → verify, up to max_steps.
    progress_cb(step, thought, action, small_jpg_path) after each step.
    Returns {status, steps, summary, final_screenshot, timings, needs_confirm?}."""
    model = model or get_brain_model()
    steps: list[dict] = []
    history: list[str] = []
    sigs: list[str] = []
    last_result = ""
    status, summary, needs_confirm = "max_steps", "", None
    small = None
    t_start = time.time()
    if not desktop.display_ok():
        return {"status": "error", "steps": [], "summary": f"display {desktop.DISPLAY} not reachable",
                "final_screenshot": None}
    for n in range(1, max_steps + 1):
        t0 = time.time()
        try:
            full, small = desktop.screenshot(scale_max=STEP_IMG_EDGE)
        except Exception as exc:  # noqa: BLE001
            status, summary = "error", f"screenshot failed: {exc}"
            break
        t1 = time.time()
        els = desktop.a11y_tree(400)
        window = desktop.active_window_title()
        t2 = time.time()
        try:
            step = _brain_step(task, window, _elements_text(els), last_result, small, history, model)
        except Exception as exc:  # noqa: BLE001
            status, summary = "error", f"brain call failed: {type(exc).__name__}: {exc}"
            break
        t3 = time.time()
        act = step["action"] or "wait"
        sig = f"{act}|{step.get('target','')}|{step.get('text','')}"
        if act == "done":
            status, summary = "done", step.get("done_summary") or step.get("thought") or "done"
            steps.append({"n": n, **step, "result": "done", "screenshot": small,
                          "t": {"shot": round(t1 - t0, 2), "a11y": round(t2 - t1, 2),
                                "brain": round(t3 - t2, 2), "act": 0.0}})
            if progress_cb:
                progress_cb(n, step["thought"], "done", small)
            break
        result, confirm = _execute(step, els, approved)
        t4 = time.time()
        steps.append({"n": n, **step, "result": result, "screenshot": small,
                      "t": {"shot": round(t1 - t0, 2), "a11y": round(t2 - t1, 2),
                            "brain": round(t3 - t2, 2), "act": round(t4 - t3, 2)}})
        log.info("desktop step %d: %s %r -> %s (shot %.2fs a11y %.2fs brain %.2fs act %.2fs)",
                 n, act, step.get("target") or step.get("text"), result,
                 t1 - t0, t2 - t1, t3 - t2, t4 - t3)
        if progress_cb:
            try:
                progress_cb(n, step["thought"], f"{act} {step.get('target') or step.get('text') or ''}".strip(), small)
            except Exception as exc:  # noqa: BLE001
                log.warning("progress_cb failed: %s", exc)
        if confirm:
            status, needs_confirm, summary = "needs_confirm", confirm, f"paused before {confirm}"
            break
        history.append(f"{act} {step.get('target') or step.get('text') or ''}".strip() + (" (failed)" if "failed" in result else ""))
        last_result = result
        sigs.append(sig)
        if len(sigs) >= 3 and sigs[-1] == sigs[-2] == sigs[-3]:
            status, summary = "stuck", f"same action 3x in a row: {sig}"
            break
        time.sleep(0.6)
    if status == "max_steps" and not summary:
        summary = f"stopped after {max_steps} steps without 'done'"
    try:
        _, small = desktop.screenshot()
    except Exception:  # noqa: BLE001
        pass
    out = {"status": status, "steps": steps, "summary": summary, "final_screenshot": small,
           "elapsed_s": round(time.time() - t_start, 1), "model": model}
    if needs_confirm:
        out["needs_confirm"] = needs_confirm
    if watch:
        out["watch"] = f"VNC {desktop.VNC_CONNECT} (password in {desktop.VNC_PASSWORD_FILE})"
    return out


if __name__ == "__main__":  # quick manual run: python3 tools/desktop_agent.py "task"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run_desktop_task(" ".join(sys.argv[1:]) or "open https://example.com", max_steps=6)
    print(json.dumps({k: v for k, v in res.items() if k != "steps"}, indent=2))
    for s in res["steps"]:
        print(s["n"], s["action"], s.get("target") or s.get("text"), "->", s["result"], s["t"])
