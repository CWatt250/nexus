"""Phase A (hardening) — live runtime self-facts for prompt injection.

Both screenshot bugs trace to the same gap: the chat system prompt is
built from static Markdown and never tells the model anything true about
its own running state. So when asked "what model are you?" it honestly
reports no visibility, and when it parrots a host fact ("RADV stack
healthy") nothing has actually checked it.

This module composes a small block of *probed* facts — brain model id
(core/brain.py), what's actually resident in VRAM (Ollama /api/ps), and
the inference backend (detected from the ollama unit, not asserted) —
that callers inject into the system prompt every turn. Probe, don't
hardcode: when models.json or the GPU stack changes, this stays correct.

Everything is best-effort: any failed probe degrades to a known-safe
string rather than raising, so the chat path never breaks on it.
"""
from __future__ import annotations

import glob
import logging
import shutil
import subprocess
import time
from pathlib import Path

from core import brain

log = logging.getLogger("nexus.self_facts")

OLLAMA_URL = "http://localhost:11434"

# The host is fixed hardware — stated once, not probed every turn.
HOST = ("NIMO mini PC — AMD Ryzen AI Max+ 395 (Strix Halo, 16 Zen5 cores, "
        "Radeon 8060S iGPU gfx1151), 128 GB LPDDR5X unified memory")

# Caches: the GPU stack never changes at runtime; /api/ps is cheap but we
# still TTL it so a burst of messages doesn't each hit the socket.
_STACK_CACHE: str | None = None
_PS_CACHE: tuple[float, list[dict]] | None = None
_PS_TTL_S = 15.0


def _detect_gpu_stack() -> str:
    """Detect the Ollama inference backend ONCE, then cache.

    Authoritative tell on this box: the ollama systemd unit sets
    HIP_VISIBLE_DEVICES="" which disables the ROCm/HIP path, so inference
    falls to Vulkan/RADV (models still load into VRAM). If someone later
    re-enables ROCm (HSA_OVERRIDE_GFX_VERSION / non-empty HIP_VISIBLE_DEVICES)
    this reports ROCm instead. Falls back to a driver-presence check.
    """
    global _STACK_CACHE
    if _STACK_CACHE is not None:
        return _STACK_CACHE

    hip_disabled = False
    rocm_forced = False
    try:
        unit_files = ["/etc/systemd/system/ollama.service"]
        unit_files += glob.glob("/etc/systemd/system/ollama.service.d/*.conf")
        blob = "\n".join(
            Path(f).read_text(encoding="utf-8", errors="ignore")
            for f in unit_files if Path(f).exists()
        )
        for line in blob.splitlines():
            if "HIP_VISIBLE_DEVICES" in line:
                # Environment="HIP_VISIBLE_DEVICES="  → empty → ROCm off.
                val = line.split("HIP_VISIBLE_DEVICES", 1)[1]
                val = val.split("=", 1)[1] if "=" in val else ""
                if val.strip().strip('"').strip("'") in ("", "-1"):
                    hip_disabled = True
            if "HSA_OVERRIDE_GFX_VERSION" in line:
                rocm_forced = True
    except Exception as exc:  # pragma: no cover — best-effort
        log.debug("gpu stack unit probe failed: %s", exc)

    if rocm_forced and not hip_disabled:
        stack = "ROCm (HIP) via Ollama + llama.cpp"
    elif hip_disabled:
        stack = "Vulkan / Mesa RADV via Ollama + llama.cpp (ROCm present but disabled)"
    else:
        # No clear signal from the unit — fall back to driver presence.
        if shutil.which("vulkaninfo"):
            stack = "Vulkan / Mesa RADV via Ollama + llama.cpp"
        elif shutil.which("rocminfo"):
            stack = "ROCm (HIP) via Ollama + llama.cpp"
        else:
            stack = "local GPU via Ollama + llama.cpp"
    _STACK_CACHE = stack
    return stack


def _resident_models() -> list[dict]:
    """Ollama /api/ps → [{name, vram_gb}], newest probe cached for _PS_TTL_S.
    Returns [] when Ollama is unreachable."""
    global _PS_CACHE
    now = time.monotonic()
    if _PS_CACHE is not None and now - _PS_CACHE[0] < _PS_TTL_S:
        return _PS_CACHE[1]
    models: list[dict] = []
    try:
        import httpx  # noqa: PLC0415
        with httpx.Client(timeout=2) as client:
            r = client.get(f"{OLLAMA_URL}/api/ps")
        if r.status_code == 200:
            for m in r.json().get("models", []):
                models.append({
                    "name": m.get("name", "?"),
                    "vram_gb": round((m.get("size_vram") or 0) / 1e9, 1),
                })
    except Exception as exc:
        log.debug("/api/ps probe failed: %s", exc)
    _PS_CACHE = (now, models)
    return models


def self_facts_block() -> str:
    """Compact, probed self-facts for system-prompt injection. Always
    returns a usable string (worst case: brain id from models.json +
    host), never raises."""
    brain_model = brain.get_brain_model()
    resident = _resident_models()
    stack = _detect_gpu_stack()

    # Is the brain actually loaded right now? Grounds any "healthy" claim
    # in real /api/ps data instead of asserting it.
    brain_short = brain_model.split("/")[-1]
    loaded = next((m for m in resident
                   if brain_model in m["name"] or m["name"] in brain_model
                   or brain_short in m["name"]), None)
    if loaded:
        serving = f"{brain_model} — resident & serving ({loaded['vram_gb']} GB VRAM)"
    elif resident:
        serving = (f"{brain_model} (configured brain; not currently resident — "
                   f"first reply will cold-load it)")
    else:
        serving = f"{brain_model} (configured brain; Ollama state unverified)"

    others = [m["name"] for m in resident
              if not (brain_model in m["name"] or m["name"] in brain_model
                      or brain_short in m["name"])]

    lines = [
        "## Your runtime (live-probed — these are TRUE facts about yourself; "
        "when asked what model/hardware you run on, answer from these and do "
        "NOT say you lack visibility):",
        f"- Model serving this conversation: {serving}",
        f"- Host: {HOST}",
        f"- Inference stack: {stack}",
    ]
    if others:
        lines.append(f"- Also loaded in VRAM: {', '.join(others)}")
    return "\n".join(lines)
