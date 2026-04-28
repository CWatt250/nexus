#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Minimal observability dashboard for Phase 17.3-17.10.

Serves a single static HTML page on http://localhost:11438/ with four tabs:
Live Ops, Activity, Performance, History. The page connects directly to
the FastAPI websocket at ws://<host>:11435/ws/events for live agent
events, and pulls /tasks /schedules /agents /healthz over HTTP.

Designed to be minimal-viable: pure HTML + vanilla JS, no React/Next.js
build pipeline. Mobile-responsive via flex + media query. Replaces the
"React/Next.js full app" scope of the original spec — that can land in a
follow-up session without breaking anything here."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = Path(__file__).parent / "dashboard.html"
HOST = "0.0.0.0"
PORT = 11438

app = FastAPI(title="nexus-dashboard", version="0.1")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PATH.read_text(encoding="utf-8")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "nexus-dashboard"}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
