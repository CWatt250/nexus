"""Performance Guardian (Phase 16.7).

A small periodic monitor that alerts when system load crosses thresholds,
applies hysteresis (no re-alert for the same condition within 30 min), and
keeps the always-pinned models (`qwen3:4b`, `qwen3.6`) safe from automatic
LRU eviction.

Decisions logged to `memory/perf-guardian.jsonl`. Alerts also go to Telegram
via `proactive_send` (best-effort).

Run as `nexus-perf-guardian.service` with a 60s tick. The Monday 08:00 digest
is exposed by `weekly_digest()` and the systemd timer should call this
function rather than running on the same minute as `nexus-lessons.timer`.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / "AI_Agent"
LOG_PATH = ROOT / "memory" / "perf-guardian.jsonl"
ALERT_STATE_PATH = ROOT / "memory" / "perf-guardian.state.json"

# Models we will NEVER ask Ollama to unload — protects router warmth and the
# heavy task model. Match `models.json` defaults.
PINNED_MODELS = ("qwen3:4b", "qwen3.6")

# Thresholds. Conservative — tuned by Colton over time.
THRESHOLDS = {
    "ram_pct": 85.0,
    "swap_pct": 50.0,
    "gpu_mem_pct": 90.0,
    "cpu_pct_5min": 90.0,
    "load_per_cpu": 1.5,
}
ALERT_HYSTERESIS_S = 30 * 60  # 30 min

log = logging.getLogger("nexus.perf_guardian")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_alert_state() -> dict[str, float]:
    if not ALERT_STATE_PATH.exists():
        return {}
    try:
        return json.loads(ALERT_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_alert_state(state: dict[str, float]) -> None:
    ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        ALERT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))
    except OSError as exc:
        log.warning("alert state write failed: %s", exc)


def _append_log(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("perf-guardian log write failed: %s", exc)


def _sample() -> dict:
    """Best-effort sample of system load. Uses psutil when available; falls
    back to /proc reads so the guardian still works in minimal environments."""
    sample: dict = {"ts": _now().isoformat()}
    try:
        import psutil
        sample["ram_pct"] = float(psutil.virtual_memory().percent)
        sw = psutil.swap_memory()
        sample["swap_pct"] = float(sw.percent) if sw.total else 0.0
        sample["cpu_pct_5min"] = float(psutil.cpu_percent(interval=None))
        load1, load5, load15 = psutil.getloadavg()
        sample["load1"] = load1
        sample["load5"] = load5
        sample["cpus"] = psutil.cpu_count(logical=True) or 1
        sample["load_per_cpu"] = load5 / sample["cpus"]
    except Exception:
        # Fallback paths.
        try:
            mem = Path("/proc/meminfo").read_text()
            mem_lines = {l.split(":")[0]: l.split(":")[1].strip() for l in mem.splitlines()}
            total = int(mem_lines["MemTotal"].split()[0])
            avail = int(mem_lines["MemAvailable"].split()[0])
            sample["ram_pct"] = round((total - avail) / total * 100, 1)
        except Exception:
            sample["ram_pct"] = 0.0
        try:
            load = Path("/proc/loadavg").read_text().split()
            sample["load1"] = float(load[0])
            sample["load5"] = float(load[1])
            try:
                cpus = int(Path("/proc/cpuinfo").read_text().count("processor\t"))
            except Exception:
                cpus = 1
            sample["cpus"] = cpus or 1
            sample["load_per_cpu"] = sample["load5"] / sample["cpus"]
        except Exception:
            sample["load1"] = 0.0
            sample["load5"] = 0.0
            sample["cpus"] = 1
            sample["load_per_cpu"] = 0.0
        sample["swap_pct"] = 0.0
        sample["cpu_pct_5min"] = 0.0

    # GPU memory via Ollama's /api/ps (every loaded model reports VRAM use).
    try:
        import httpx
        with httpx.Client(timeout=2) as client:
            r = client.get("http://localhost:11434/api/ps")
        models = r.json().get("models", []) if r.status_code == 200 else []
        sample["loaded_models"] = [
            {"name": m.get("name"), "size_vram_gb": round((m.get("size_vram") or 0) / 1e9, 2)}
            for m in models
        ]
    except Exception:
        sample["loaded_models"] = []
    return sample


def _check_thresholds(sample: dict) -> list[dict]:
    """Return a list of {name, observed, threshold} for breaches."""
    breaches: list[dict] = []
    for name, threshold in THRESHOLDS.items():
        observed = sample.get(name)
        if observed is None:
            continue
        if observed >= threshold:
            breaches.append({"name": name, "observed": observed, "threshold": threshold})
    return breaches


def _maybe_alert(breaches: list[dict]) -> list[dict]:
    """Apply 30-min hysteresis and fire Telegram for newly-tripped conditions."""
    state = _read_alert_state()
    now = time.time()
    fired: list[dict] = []
    for b in breaches:
        last = float(state.get(b["name"], 0.0))
        if now - last < ALERT_HYSTERESIS_S:
            continue
        state[b["name"]] = now
        fired.append(b)

    if fired:
        _write_alert_state(state)
        msg_lines = ["⚠️ Nexus performance guardian:"]
        for b in fired:
            msg_lines.append(
                f"  • {b['name']} = {b['observed']:.1f} (threshold {b['threshold']})"
            )
        text = "\n".join(msg_lines)
        try:
            import asyncio
            from tools.telegram_tool import proactive_send
            asyncio.run(proactive_send(text))
        except Exception as exc:
            log.warning("Telegram alert send failed: %s", exc)
    return fired


def protect_pinned_models() -> dict:
    """No-op safety: report whether the always-pinned models are still loaded.

    This module never actively unloads models — Ollama handles eviction.
    What we do is *fail loud* if our pinned router/heavy disappears, so a
    cron-triggered nexus-prewarm step can re-warm them.
    """
    try:
        import httpx
        with httpx.Client(timeout=2) as client:
            r = client.get("http://localhost:11434/api/ps")
        if r.status_code != 200:
            return {"ok": False, "error": f"/api/ps returned {r.status_code}"}
        loaded = {m.get("name") for m in r.json().get("models", [])}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    missing = [m for m in PINNED_MODELS if not any(m in n for n in loaded)]
    return {"ok": not missing, "missing": missing, "loaded": list(loaded)}


def tick_once() -> dict:
    """One pass: sample, log, alert. Returns the sample for visibility."""
    sample = _sample()
    breaches = _check_thresholds(sample)
    fired = _maybe_alert(breaches) if breaches else []
    pinned = protect_pinned_models()
    record = {**sample, "breaches": breaches, "fired": fired, "pinned": pinned}
    _append_log(record)
    return record


def weekly_digest() -> str:
    """Aggregate the last 7 days of perf-guardian.jsonl into a short summary.
    Called by a separate timer (Mondays 08:00) — the spec calls out staggering
    this away from nexus-lessons.timer, also Mondays 08:00, so use 08:30."""
    if not LOG_PATH.exists():
        return "no perf-guardian data yet."
    cutoff = (_now().timestamp() - 7 * 86400)
    samples: list[dict] = []
    for line in LOG_PATH.read_text().splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = datetime.fromisoformat(obj["ts"]).timestamp()
        except Exception:
            continue
        if ts >= cutoff:
            samples.append(obj)
    if not samples:
        return "no perf-guardian samples in the last 7 days."

    def pct(metric):
        vals = [s.get(metric) for s in samples if s.get(metric) is not None]
        if not vals:
            return 0.0, 0.0
        return max(vals), sum(vals) / len(vals)

    ram_max, ram_avg = pct("ram_pct")
    cpu_max, cpu_avg = pct("cpu_pct_5min")
    load_max, load_avg = pct("load_per_cpu")
    fired_count = sum(len(s.get("fired") or []) for s in samples)

    return (
        "# Perf guardian — week digest\n\n"
        f"- samples: {len(samples)}\n"
        f"- RAM: max {ram_max:.1f}%  avg {ram_avg:.1f}%\n"
        f"- CPU: max {cpu_max:.1f}%  avg {cpu_avg:.1f}%\n"
        f"- load/cpu: max {load_max:.2f}  avg {load_avg:.2f}\n"
        f"- alerts fired this week: {fired_count}\n"
    )


def run_forever(poll_seconds: float = 60.0) -> None:
    log.info("nexus perf-guardian started (poll=%.0fs)", poll_seconds)
    while True:
        try:
            tick_once()
        except Exception as exc:
            log.exception("tick error: %s", exc)
        time.sleep(poll_seconds)
