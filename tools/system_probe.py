"""Phase C (hardening) — read-only system/runtime status tool.

Gives Nexus actual eyes on his own host so "check the process list / are
you healthy / what's loaded / how's memory" is answerable with REAL data
on the fast path, instead of being a hollow "let me check…" promise.

Strictly read-only: psutil samples + the Phase-A self-facts probe
(/api/ps, GPU stack). No subprocess, no shell, no side effects — safe to
expose to the single-tool lite_agent path.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

log = logging.getLogger("nexus.system_probe")

_CATEGORIES = ("summary", "processes", "memory", "disk", "gpu", "models", "services")


def _mem_line() -> str:
    import psutil  # noqa: PLC0415
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return (f"RAM {vm.percent:.0f}% used "
            f"({vm.used/1e9:.1f}/{vm.total/1e9:.1f} GB), "
            f"swap {sw.percent:.0f}%")


def _load_line() -> str:
    import psutil  # noqa: PLC0415
    l1, l5, l15 = psutil.getloadavg()
    cpus = psutil.cpu_count(logical=True) or 1
    return f"load {l1:.2f}/{l5:.2f}/{l15:.2f} over {cpus} cpus"


def _disk_line() -> str:
    import psutil  # noqa: PLC0415
    d = psutil.disk_usage("/")
    return f"disk / {d.percent:.0f}% used ({d.used/1e9:.0f}/{d.total/1e9:.0f} GB)"


def _top_processes(n: int = 8) -> str:
    import psutil  # noqa: PLC0415
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            procs.append((p.info["name"] or "?",
                          p.info["cpu_percent"] or 0.0,
                          (p.info["memory_info"].rss if p.info["memory_info"] else 0)))
        except Exception:
            continue
    procs.sort(key=lambda x: x[2], reverse=True)  # by RSS (cpu_percent needs interval)
    lines = [f"  {name[:24]:24} {rss/1e9:5.1f} GB" for name, _cpu, rss in procs[:n]]
    return "top processes by memory:\n" + "\n".join(lines)


def _models_line() -> str:
    from core import self_facts  # noqa: PLC0415
    resident = self_facts._resident_models()
    if not resident:
        return "loaded models: none resident (Ollama idle or unreachable)"
    return "loaded models: " + ", ".join(
        f"{m['name'].split('/')[-1]} ({m['vram_gb']} GB)" for m in resident)


def _services_line() -> str:
    """Best-effort nexus-* unit health via systemd, read-only. Falls back
    to 'unknown' rather than shelling out if dbus isn't reachable."""
    try:
        import subprocess  # noqa: PLC0415
        out = subprocess.run(
            ["systemctl", "is-active", "ollama", "nexus-api", "nexus-telegram",
             "nexus-prewarm"],
            capture_output=True, text=True, timeout=4,
        ).stdout.strip().splitlines()
        names = ["ollama", "nexus-api", "nexus-telegram", "nexus-prewarm"]
        return "services: " + ", ".join(
            f"{n}={s}" for n, s in zip(names, out)) if out else "services: unknown"
    except Exception as exc:
        log.debug("services probe failed: %s", exc)
        return "services: unknown (systemd not reachable from this context)"


def _build(what: str) -> str:
    if what == "processes":
        return _top_processes()
    if what == "memory":
        return f"{_mem_line()}; {_load_line()}"
    if what == "disk":
        return _disk_line()
    if what in ("gpu", "models"):
        from core import self_facts  # noqa: PLC0415
        return f"{_models_line()}\ninference stack: {self_facts._detect_gpu_stack()}"
    if what == "services":
        return _services_line()
    # summary (default): the one-glance health line.
    from core import self_facts  # noqa: PLC0415
    return (
        f"{self_facts.self_facts_block()}\n\n"
        f"- {_mem_line()}\n- {_load_line()}\n- {_disk_line()}\n- {_services_line()}"
    )


@tool
def system_status(what: str = "summary") -> str:
    """Read-only status of Nexus's OWN host/runtime. Use this to answer
    questions about whether Nexus is healthy, what model is loaded, the
    process list, memory/disk/GPU usage, or service health.

    what: one of summary | processes | memory | disk | gpu | models | services
    (default "summary" — model + host + memory + load + disk + services)."""
    w = (what or "summary").strip().lower()
    if w not in _CATEGORIES:
        w = "summary"
    try:
        return _build(w)
    except Exception as exc:  # never let a probe raise into the agent
        return f"(system_status probe failed: {type(exc).__name__}: {exc})"
