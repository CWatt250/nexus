#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 17.5 — iOS Liquid Glass dashboard server.

Serves dashboard_v2/ (single-file React via CDN, Tailwind CDN, vanilla
WS). Falls back to the legacy minimal HTML at /legacy so the old URL
keeps working while v2 is being shaped.

The dashboard talks directly to:
    ws://<host>:11435/ws/events            — live event bus
    GET  /api/dispatches                   — queue + recent results
    POST /api/dispatch                     — new dispatch
    POST /api/dispatch/approve | cancel    — pending controls
    GET  /api/services                     — nexus-* health snapshot
    POST /api/restart                      — restart selected services
    GET  /api/memory/retros                — recent retros
    POST /chat                             — Nexus chat passthrough
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

ROOT = Path(__file__).resolve().parent.parent
V2_DIR = ROOT / "dashboard_v2"
LEGACY_HTML = Path(__file__).parent / "dashboard.html"
HOST = "0.0.0.0"
PORT = 11438

app = FastAPI(title="nexus-dashboard", version="0.2 — liquid-glass")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Dashboard v2 if built, else legacy."""
    v2 = V2_DIR / "index.html"
    if v2.exists():
        return FileResponse(v2, media_type="text/html")
    return HTMLResponse(LEGACY_HTML.read_text(encoding="utf-8"))


@app.get("/legacy", response_class=HTMLResponse)
async def legacy():
    """Phase 17.3 minimal vanilla-JS dashboard, kept as a known-good fallback."""
    return LEGACY_HTML.read_text(encoding="utf-8")


@app.get("/manifest.json")
async def manifest():
    p = V2_DIR / "manifest.json"
    if not p.exists():
        return JSONResponse({"error": "manifest missing"}, status_code=404)
    return FileResponse(p, media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    p = V2_DIR / "sw.js"
    if not p.exists():
        return PlainTextResponse("", status_code=404)
    return FileResponse(p, media_type="application/javascript")


@app.get("/icon-{size}.svg")
async def icon(size: str):
    p = V2_DIR / f"icon-{size}.svg"
    if not p.exists():
        return Response(status_code=404)
    return FileResponse(p, media_type="image/svg+xml")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "nexus-dashboard", "v2_present": (V2_DIR / "index.html").exists()}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
