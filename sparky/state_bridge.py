#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Sparky State Bridge — FastAPI server for Nexus-to-Sparky state communication."""
from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Sparky State Bridge", version="1.1.0")

# Allow CORS for Electron overlay
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SparkyState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    EXCITED = "excited"
    HAPPY = "happy"
    ERROR = "error"
    SLEEPING = "sleeping"
    WHAMMY = "whammy"
    TALKING = "talking"


class StateUpdate(BaseModel):
    state: SparkyState
    message: Optional[str] = None
    duration: Optional[int] = None  # Duration in milliseconds, None = use default


class CurrentState(BaseModel):
    state: SparkyState
    message: Optional[str]
    updated_at: float
    is_speaking: bool


# ---------------------------------------------------------------------------
# State TTLs
#
# Non-idle states auto-revert to idle unless they represent an ongoing
# condition (thinking/working/sleeping/talking). Callers can override with
# the `duration` field on StateUpdate; passing duration=0 disables the
# revert.
# ---------------------------------------------------------------------------

DEFAULT_TTL_SECONDS = 3.0
STATE_TTL_SECONDS: dict[SparkyState, float] = {
    SparkyState.HAPPY: 2.0,
    SparkyState.WHAMMY: 2.0,
    SparkyState.ERROR: 4.0,
    SparkyState.EXCITED: DEFAULT_TTL_SECONDS,
}
# These states stay until something else changes them.
PERSISTENT_STATES: set[SparkyState] = {
    SparkyState.IDLE,
    SparkyState.THINKING,
    SparkyState.WORKING,
    SparkyState.SLEEPING,
    SparkyState.TALKING,
}


# Global state
_current_state = CurrentState(
    state=SparkyState.IDLE,
    message=None,
    updated_at=time.time(),
    is_speaking=False,
)
_revert_task: Optional[asyncio.Task] = None


def _cancel_revert() -> None:
    global _revert_task
    if _revert_task is not None and not _revert_task.done():
        _revert_task.cancel()
    _revert_task = None


def _go_idle() -> None:
    global _current_state
    _current_state = CurrentState(
        state=SparkyState.IDLE,
        message=None,
        updated_at=time.time(),
        is_speaking=False,
    )


def _resolve_ttl(update: StateUpdate) -> Optional[float]:
    """Pick the TTL (seconds) for a state. Caller-supplied `duration` (ms)
    wins. Persistent states ignore TTLs entirely."""
    if update.state in PERSISTENT_STATES:
        return None
    if update.duration is not None:
        if update.duration <= 0:
            return None
        return update.duration / 1000.0
    return STATE_TTL_SECONDS.get(update.state, DEFAULT_TTL_SECONDS)


async def _revert_later(delay: float, generation: float) -> None:
    """Sleep `delay` seconds then revert to idle — unless another state was
    set in the meantime (detected by comparing `updated_at`)."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    if _current_state.updated_at == generation and _current_state.state != SparkyState.IDLE:
        _go_idle()


@app.get("/")
async def root():
    """Health check."""
    return {"status": "ok", "service": "sparky-state-bridge"}


@app.get("/state")
async def get_state() -> CurrentState:
    """Get current Sparky state (polled by Electron overlay)."""
    return _current_state


@app.post("/state")
async def set_state(update: StateUpdate) -> dict:
    """Set Sparky state (called by Nexus). Non-persistent states
    automatically revert to idle after their TTL."""
    global _current_state, _revert_task
    _cancel_revert()
    _current_state = CurrentState(
        state=update.state,
        message=update.message,
        updated_at=time.time(),
        is_speaking=update.state == SparkyState.TALKING,
    )
    ttl = _resolve_ttl(update)
    if ttl is not None:
        stamp = _current_state.updated_at
        _revert_task = asyncio.create_task(_revert_later(ttl, stamp))
    return {"success": True, "state": _current_state.state, "revert_in": ttl}


@app.post("/reset")
async def reset_state() -> CurrentState:
    """Immediately return Sparky to idle and cancel any pending auto-revert."""
    _cancel_revert()
    _go_idle()
    return _current_state


@app.post("/speaking/start")
async def start_speaking() -> dict:
    """Signal that Nexus has started speaking (for mouth sync)."""
    global _current_state
    _cancel_revert()
    _current_state = CurrentState(
        state=SparkyState.TALKING,
        message=_current_state.message,
        updated_at=time.time(),
        is_speaking=True,
    )
    return {"success": True, "is_speaking": True}


@app.post("/speaking/stop")
async def stop_speaking() -> dict:
    """Signal that Nexus has stopped speaking."""
    _cancel_revert()
    _go_idle()
    return {"success": True, "is_speaking": False}


# Convenience endpoints for common state transitions
@app.post("/thinking")
async def set_thinking(message: Optional[str] = None) -> dict:
    """Quick endpoint to set thinking state."""
    return await set_state(StateUpdate(state=SparkyState.THINKING, message=message))


@app.post("/working")
async def set_working(message: Optional[str] = None) -> dict:
    """Quick endpoint to set working state."""
    return await set_state(StateUpdate(state=SparkyState.WORKING, message=message))


@app.post("/whammy")
async def set_whammy(message: Optional[str] = None) -> dict:
    """Quick endpoint to trigger WHAMMY animation."""
    return await set_state(StateUpdate(state=SparkyState.WHAMMY, message=message))


@app.post("/happy")
async def set_happy(message: Optional[str] = None) -> dict:
    """Quick endpoint to set happy state."""
    return await set_state(StateUpdate(state=SparkyState.HAPPY, message=message))


@app.post("/error")
async def set_error(message: Optional[str] = None) -> dict:
    """Quick endpoint to set error state."""
    return await set_state(StateUpdate(state=SparkyState.ERROR, message=message))


@app.post("/idle")
async def set_idle() -> dict:
    """Quick endpoint to return to idle state."""
    return await set_state(StateUpdate(state=SparkyState.IDLE))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=11437)
