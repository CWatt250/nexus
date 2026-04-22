#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Sparky State Bridge — FastAPI server for Nexus-to-Sparky state communication."""
from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Sparky State Bridge", version="1.0.0")

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
    duration: Optional[int] = None  # Duration in milliseconds, None = until changed


class CurrentState(BaseModel):
    state: SparkyState
    message: Optional[str]
    updated_at: float
    is_speaking: bool


# Global state
_current_state = CurrentState(
    state=SparkyState.IDLE,
    message=None,
    updated_at=time.time(),
    is_speaking=False,
)


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
    """Set Sparky state (called by Nexus)."""
    global _current_state
    _current_state = CurrentState(
        state=update.state,
        message=update.message,
        updated_at=time.time(),
        is_speaking=update.state == SparkyState.TALKING,
    )
    return {"success": True, "state": _current_state.state}


@app.post("/speaking/start")
async def start_speaking() -> dict:
    """Signal that Nexus has started speaking (for mouth sync)."""
    global _current_state
    _current_state.is_speaking = True
    _current_state.state = SparkyState.TALKING
    _current_state.updated_at = time.time()
    return {"success": True, "is_speaking": True}


@app.post("/speaking/stop")
async def stop_speaking() -> dict:
    """Signal that Nexus has stopped speaking."""
    global _current_state
    _current_state.is_speaking = False
    _current_state.state = SparkyState.IDLE
    _current_state.updated_at = time.time()
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
