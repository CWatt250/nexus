"""Phase 3 — Nexus's own headless desktop on Xvfb :99.

Low-level primitives against the :99 display (env NEXUS_DESKTOP_DISPLAY
overrides): screenshot + downscale, window listing/focus (wmctrl/xdotool),
mouse/keyboard via xdotool, Chrome URL open, and an AT-SPI accessibility
tree (pyatspi runs in a /usr/bin/python3 subprocess because the venv has
no system site-packages).

Coordinate spaces: every action takes FULL-resolution coordinates. The
last screenshot records its downscale factor; use `to_full(x, y)` to map a
point from the small (≤1024 px) image back to the screen.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

log = logging.getLogger("nexus.desktop")

ROOT = Path.home() / "AI_Agent"
DISPLAY = os.environ.get("NEXUS_DESKTOP_DISPLAY", ":99")
SHOT_DIR = ROOT / "output" / "desktop"
CHROME_PROFILE = Path(os.environ.get("NEXUS_CHROME_PROFILE",
                                     str(ROOT / "desktop" / "chrome-profile")))
VNC_CONNECT = "100.124.210.84:5900"
VNC_PASSWORD_FILE = Path.home() / ".vnc" / "nexus-vnc-password.txt"

# Same list as computer_use_tool — refused unless approve=True.
DANGEROUS_KEYS = {"delete", "backspace", "ctrl+a", "ctrl+shift+del", "alt+f4"}

# friendly name -> xdotool keysym
_KEYSYMS = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "del": "Delete", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "page_up": "Prior", "page_down": "Next", "insert": "Insert", "menu": "Menu",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "super": "super", "win": "super", "cmd": "ctrl", "meta": "alt",
}

# metadata of the most recent screenshot (scale factor for coordinate mapping)
LAST_SHOT: dict = {}


# ── plumbing ───────────────────────────────────────────────────────────
def _env() -> dict:
    e = dict(os.environ)
    e["DISPLAY"] = DISPLAY
    e.pop("WAYLAND_DISPLAY", None)
    return e


def _run(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=_env(), capture_output=True, text=True,
                          timeout=timeout)


def _xdo(*args: str, timeout: float = 10) -> str:
    p = _run(["xdotool", *args], timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args[:2])} failed: {p.stderr.strip()[:200]}")
    return p.stdout.strip()


def display_ok() -> bool:
    try:
        return _run(["xdpyinfo"], timeout=3).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def screen_size() -> tuple[int, int]:
    try:
        out = _run(["xdpyinfo"], timeout=3).stdout
        m = re.search(r"dimensions:\s+(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return 1920, 1080


# ── screenshot ─────────────────────────────────────────────────────────
def downscale(src: str | Path, dst: str | Path | None = None,
              scale_max: int = 1024, quality: int = 85) -> tuple[str, float]:
    """Shrink an image to ≤scale_max on its long edge, JPEG q85.
    Returns (small_path, scale) where scale = full_px / small_px (≥1)."""
    from PIL import Image  # noqa: PLC0415
    src = Path(src)
    dst = Path(dst) if dst else src.with_suffix(".small.jpg")
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = max(w, h) / float(scale_max)
        if scale > 1.0:
            im = im.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
        else:
            scale = 1.0
        im.save(dst, "JPEG", quality=quality, optimize=True)
    return str(dst), scale


def screenshot(path: str | Path | None = None, scale_max: int = 1024
               ) -> tuple[str, str]:
    """Capture :99 → (full_png_path, small_jpg_path). Records the downscale
    factor in LAST_SHOT so `to_full()` can map small-image coords back."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = SHOT_DIR / f"shot_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time()*1000)%1000:03d}.png"
    path = Path(path)
    p = _run(["scrot", "-o", str(path)], timeout=15)
    if p.returncode != 0 or not path.exists():
        p = _run(["import", "-window", "root", str(path)], timeout=15)
        if p.returncode != 0 or not path.exists():
            raise RuntimeError(f"screenshot failed on {DISPLAY}: {p.stderr.strip()[:200]}")
    small, scale = downscale(path, scale_max=scale_max)
    from PIL import Image  # noqa: PLC0415
    with Image.open(path) as im:
        full_size = im.size
    with Image.open(small) as im:
        small_size = im.size
    LAST_SHOT.update({"full": str(path), "small": small, "scale": scale,
                      "full_size": full_size, "small_size": small_size,
                      "ts": time.time()})
    return str(path), small


def to_full(x: float, y: float) -> tuple[int, int]:
    """Map a point in the last small screenshot to full-res screen coords."""
    s = LAST_SHOT.get("scale", 1.0)
    return int(round(x * s)), int(round(y * s))


def to_small(x: float, y: float) -> tuple[int, int]:
    s = LAST_SHOT.get("scale", 1.0)
    return int(round(x / s)), int(round(y / s))


# ── windows ────────────────────────────────────────────────────────────
def windows() -> list[dict]:
    """Visible top-level windows: [{id, title, desktop, host}]. wmctrl needs a
    window manager (openbox); falls back to an xdotool search without one."""
    out: list[dict] = []
    try:
        p = _run(["wmctrl", "-l"], timeout=3)
        if p.returncode == 0:
            for line in p.stdout.splitlines():
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    out.append({"id": parts[0], "desktop": parts[1],
                                "host": parts[2], "title": parts[3]})
            if out:
                return out
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        ids = _run(["xdotool", "search", "--onlyvisible", "--name", "."], timeout=3).stdout.split()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return out
    for wid in ids:
        try:
            title = _xdo("getwindowname", wid, timeout=2)
        except RuntimeError:
            continue
        if title:
            out.append({"id": hex(int(wid)), "desktop": "0", "host": "?", "title": title})
    return out


def active_window_title() -> str:
    """Title of the focused window. With no WM there is no active window;
    fall back to the last (topmost) visible titled window."""
    try:
        return _xdo("getactivewindow", "getwindowname", timeout=2)
    except RuntimeError:
        ws = windows()
        return ws[-1]["title"] if ws else ""


def focus(title_substr: str) -> str:
    """Focus + raise the first window whose title contains `title_substr`
    (case-insensitive). wmctrl -a works under openbox; xdotool fallback
    covers the WM-less case."""
    p = _run(["wmctrl", "-a", title_substr], timeout=3)
    if p.returncode == 0:
        return f"focused window matching {title_substr!r}"
    needle = title_substr.lower()
    for w in windows():
        if needle in w["title"].lower():
            wid = str(int(w["id"], 16))
            try:
                _run(["xdotool", "windowactivate", "--sync", wid], timeout=3)
            except subprocess.TimeoutExpired:
                pass
            _run(["xdotool", "windowraise", wid], timeout=3)
            _run(["xdotool", "windowfocus", "--sync", wid], timeout=3)
            return f"focused {w['title'][:60]!r}"
    return f"no window matching {title_substr!r}"


# ── mouse / keyboard (full-res coordinates) ────────────────────────────
_BUTTONS = {"left": "1", "middle": "2", "right": "3"}


def move(x: int, y: int) -> str:
    _xdo("mousemove", str(int(x)), str(int(y)))
    return f"moved to ({int(x)}, {int(y)})"


def click(x: int, y: int, button: str = "left") -> str:
    b = _BUTTONS.get(button, button)
    _xdo("mousemove", str(int(x)), str(int(y)), "click", str(b))
    return f"clicked {button} at ({int(x)}, {int(y)})"


def double_click(x: int, y: int) -> str:
    _xdo("mousemove", str(int(x)), str(int(y)),
         "click", "--repeat", "2", "--delay", "80", "1")
    return f"double-clicked at ({int(x)}, {int(y)})"


def type_text(text: str) -> str:
    if not text:
        return "nothing to type"
    if len(text) > 2000:
        return "refused: text > 2000 chars"
    _xdo("type", "--delay", "12", "--", text, timeout=60)
    return f"typed {len(text)} chars"


def _keysym(combo: str) -> str:
    parts = [p.strip() for p in combo.replace(" ", "").split("+") if p.strip()]
    mapped = []
    for p in parts:
        lp = p.lower()
        if lp in _KEYSYMS:
            mapped.append(_KEYSYMS[lp])
        elif re.fullmatch(r"f\d{1,2}", lp):
            mapped.append(lp.upper())
        elif len(p) == 1:
            mapped.append(p)
        else:
            mapped.append(p)
    return "+".join(mapped)


def press(key: str, approve: bool = False) -> str:
    """Press a key or combo ('enter', 'ctrl+l', 'alt+Tab'). DANGEROUS_KEYS
    are refused unless approve=True."""
    norm = key.strip().lower().replace(" ", "")
    if norm in DANGEROUS_KEYS and not approve:
        return f"SAFETY: key {key!r} is destructive — re-call with approve=True"
    _xdo("key", "--clearmodifiers", _keysym(key))
    return f"pressed {key}"


def scroll(dx: int = 0, dy: int = 0, x: int | None = None, y: int | None = None) -> str:
    """Scroll by wheel clicks. dy>0 = down, dy<0 = up; dx>0 = right."""
    if x is not None and y is not None:
        _xdo("mousemove", str(int(x)), str(int(y)))
    if dy:
        _xdo("click", "--repeat", str(abs(int(dy))), "--delay", "30", "5" if dy > 0 else "4")
    if dx:
        _xdo("click", "--repeat", str(abs(int(dx))), "--delay", "30", "7" if dx > 0 else "6")
    return f"scrolled dx={dx} dy={dy}"


# ── chrome ─────────────────────────────────────────────────────────────
CDP_PORT = int(os.environ.get("NEXUS_CHROME_CDP_PORT", "9222"))
CHROME_FLAGS = [
    "--ozone-platform=x11",
    f"--remote-debugging-port={CDP_PORT}",  # 127.0.0.1 only — DOM element source
    "--window-size=1920,1040", "--window-position=0,0",
    "--no-first-run", "--no-default-browser-check",
    "--disable-gpu-sandbox", "--force-renderer-accessibility",
    "--password-store=basic", "--disable-features=TranslateUI",
    "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
]


def chrome_window() -> Optional[dict]:
    for w in windows():
        t = w["title"].lower()
        if "google chrome" in t or "chromium" in t or t.endswith("- chrome"):
            return w
    return None


def ensure_chrome(url: str = "about:blank", profile: Path | None = None,
                  wait_s: float = 12) -> str:
    """Launch Chrome on :99 if no Chrome window is up. Returns a status line."""
    if chrome_window():
        return "chrome already up"
    prof = Path(profile or CHROME_PROFILE)
    prof.mkdir(parents=True, exist_ok=True)
    cmd = ["google-chrome", f"--user-data-dir={prof}", *CHROME_FLAGS, url]
    subprocess.Popen(cmd, env=_env(), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if chrome_window():
            time.sleep(1.0)
            return "chrome launched"
        time.sleep(0.5)
    return "chrome launch: no window after wait"


def open_url(url: str) -> str:
    """Navigate the Chrome window on :99 to `url` (ctrl+l, type, Return)."""
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url):
        url = "https://" + url
    status = ensure_chrome(url)
    if status == "chrome launched":
        return f"opened {url} (fresh chrome)"
    focus("chrome")
    time.sleep(0.15)
    _xdo("key", "--clearmodifiers", "ctrl+l")
    time.sleep(0.15)
    _xdo("type", "--delay", "8", "--", url, timeout=30)
    _xdo("key", "Return")
    return f"opened {url}"


# ── accessibility tree (pyatspi in a system-python subprocess) ─────────
_A11Y_SCRIPT = r'''
import json, sys
try:
    import pyatspi
except Exception as e:
    print(json.dumps({"error": "pyatspi import failed: %s" % e})); sys.exit(0)
MAX = int(sys.argv[1]); want = sys.argv[2].lower()
ACTION_ROLES = {"push button","link","entry","text","password text","check box",
    "radio button","combo box","menu item","page tab","tab","list item","toggle button",
    "spin button","slider","table cell","heading","menu","button","search",
    "check menu item","radio menu item","tree item","document web","image"}
out = []; err = None
def visible(node):
    try:
        st = node.getState()
        return st.contains(pyatspi.STATE_VISIBLE) and st.contains(pyatspi.STATE_SHOWING)
    except Exception:
        return False
def ext(node):
    try:
        c = node.queryComponent(); e = c.getExtents(pyatspi.DESKTOP_COORDS)
        return int(e.x), int(e.y), int(e.width), int(e.height)
    except Exception:
        return None
def walk(root):
    stack = [(root, 0)]
    while stack and len(out) < MAX:
        node, depth = stack.pop()
        if depth > 40: continue
        try:
            role = node.getRoleName(); name = (node.name or "").strip()
        except Exception:
            continue
        if not visible(node):
            continue
        e = ext(node)
        if e and e[2] > 0 and e[3] > 0 and (role in ACTION_ROLES) and (name or role in ("entry","text","password text","combo box","search")):
            if not name:
                try: name = (node.description or "").strip()
                except Exception: pass
            out.append({"role": role, "name": name[:120], "x": e[0], "y": e[1], "w": e[2], "h": e[3]})
        try:
            n = node.childCount
        except Exception:
            n = 0
        if n > 3000: n = 3000
        for i in range(n - 1, -1, -1):
            try:
                ch = node.getChildAtIndex(i)
            except Exception:
                continue
            if ch is not None: stack.append((ch, depth + 1))
try:
    desk = pyatspi.Registry.getDesktop(0)
    apps = [desk.getChildAtIndex(i) for i in range(desk.childCount)]
    apps = [a for a in apps if a is not None]
    if want:
        pref = [a for a in apps if want in (a.name or "").lower()]
        apps = pref + [a for a in apps if a not in pref]
    for app in apps:
        for j in range(app.childCount):
            win = app.getChildAtIndex(j)
            if win is None or not visible(win): continue
            walk(win)
            if len(out) >= MAX: break
        if len(out) >= MAX: break
except Exception as e:
    err = "a11y walk failed: %s" % e
print(json.dumps({"elements": out, "error": err}))
'''


def _atspi_elements(max_nodes: int, app_hint: str, timeout: float) -> list[dict]:
    try:
        p = subprocess.run(["/usr/bin/python3", "-c", _A11Y_SCRIPT, str(max_nodes), app_hint],
                           env=_env(), capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("a11y_tree: %s", exc)
        return []
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        log.warning("a11y_tree: bad output: %s", (p.stdout or p.stderr)[:200])
        return []
    if data.get("error"):
        log.info("a11y_tree: %s", data["error"])
    return data.get("elements", []) or []


# Fallback element source: the DOM of Chrome's foreground tab over the
# DevTools port (Playwright connect_over_cdp). No sudo, no AT-SPI bus.
_DOM_JS = r"""
() => {
  const sel = 'a[href],button,input,textarea,select,summary,label,h1,h2,h3,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=radio],[role=textbox],[role=combobox],[role=option],[role=switch],[contenteditable=true]';
  const vw = window.innerWidth, vh = window.innerHeight;
  const offX = window.screenX + Math.max(0, Math.round((window.outerWidth - window.innerWidth) / 2));
  const offY = window.screenY + Math.max(0, window.outerHeight - window.innerHeight);
  const map = {a: 'link', button: 'push button', textarea: 'entry', select: 'combo box', summary: 'push button', label: 'label', h1: 'heading', h2: 'heading', h3: 'heading'};
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    const tag = el.tagName.toLowerCase();
    let role = el.getAttribute('role') || map[tag] || tag;
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      role = t === 'password' ? 'password text' : t === 'checkbox' ? 'check box' : t === 'radio' ? 'radio button'
           : (t === 'submit' || t === 'button') ? 'push button' : t === 'search' ? 'search' : 'entry';
    }
    let name = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || el.alt || el.name || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    if (!name && !['entry', 'combo box', 'password text', 'search', 'textbox'].includes(role)) continue;
    out.push({role, name, x: Math.round(r.left + offX), y: Math.round(r.top + offY), w: Math.round(r.width), h: Math.round(r.height)});
  }
  return out;
}
"""


def _dom_elements(max_nodes: int, timeout: float = 8) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        log.info("dom_elements: playwright unavailable: %s", exc)
        return []
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}",
                                                  timeout=int(timeout * 1000))
            try:
                pages = [pg for ctx in browser.contexts for pg in ctx.pages]
                page = None
                for pg in pages:
                    try:
                        if pg.evaluate("document.visibilityState") == "visible":
                            page = pg
                            break
                    except Exception:  # noqa: BLE001
                        continue
                page = page or (pages[-1] if pages else None)
                if page is None:
                    return []
                els = page.evaluate(_DOM_JS) or []
            finally:
                browser.close()  # disconnects only (connect_over_cdp)
    except Exception as exc:  # noqa: BLE001
        log.info("dom_elements: %s", str(exc).splitlines()[0][:160])
        return []
    return els[:max_nodes]


def a11y_tree(max_nodes: int = 400, app_hint: str = "chrome", timeout: float = 20
              ) -> list[dict]:
    """Visible, actionable elements [{role, name, x, y, w, h}] in screen
    coords. Primary: AT-SPI via pyatspi (covers every app, needs
    python3-pyatspi + at-spi bus). Fallback: Chrome's foreground-tab DOM
    over the DevTools port. Returns [] when neither is available."""
    els = _atspi_elements(max_nodes, app_hint, timeout)
    if els:
        return els
    return _dom_elements(max_nodes)


def find_element(query: str, elements: list[dict] | None = None,
                 min_score: float = 0.55) -> Optional[dict]:
    """Fuzzy-match `query` against a11y element names (+role). Returns the
    best element with cx/cy centre in FULL-res coords, or None."""
    q = query.strip().lower()
    if not q:
        return None
    els = elements if elements is not None else a11y_tree()
    best, best_score = None, 0.0
    for el in els:
        name = (el.get("name") or "").lower()
        role = (el.get("role") or "").lower()
        if not name:
            continue
        if q == name:
            score = 1.0
        elif q in name or name in q:
            score = 0.9 * min(len(q), len(name)) / max(len(q), len(name)) + 0.1
        else:
            score = difflib.SequenceMatcher(None, q, name).ratio()
        if role and role in q:
            score += 0.05
        if score > best_score:
            best, best_score = el, score
    if best is None or best_score < min_score:
        return None
    return {**best, "cx": best["x"] + best["w"] // 2, "cy": best["y"] + best["h"] // 2,
            "score": round(best_score, 2)}


# ── LangGraph tools ────────────────────────────────────────────────────
@tool
def desktop_screenshot() -> str:
    """Screenshot Nexus's own headless desktop (:99). Returns the full PNG
    path and a ≤1024px JPEG path suitable for a vision model, plus the
    active window title."""
    try:
        full, small = screenshot()
    except Exception as exc:
        return f"desktop screenshot failed: {type(exc).__name__}: {exc}"
    return f"full={full} small={small} active_window={active_window_title()!r}"


@tool
def desktop_click(x: int, y: int, button: str = "left", double: bool = False) -> str:
    """Click at FULL-resolution screen coords on the :99 desktop.
    button: left|right|middle. double=True for a double click."""
    try:
        return double_click(x, y) if double else click(x, y, button)
    except Exception as exc:
        return f"desktop click failed: {type(exc).__name__}: {exc}"


@tool
def desktop_type(text: str = "", key: str = "", approve: bool = False) -> str:
    """Type `text` and/or press `key` (e.g. 'enter', 'ctrl+l', 'tab') on
    the :99 desktop. Destructive keys need approve=True."""
    out = []
    try:
        if text:
            out.append(type_text(text))
        if key:
            out.append(press(key, approve=approve))
    except Exception as exc:
        out.append(f"failed: {type(exc).__name__}: {exc}")
    return "; ".join(out) or "nothing to do"


@tool
def desktop_open_url(url: str) -> str:
    """Open `url` in the Chrome window on Nexus's :99 desktop (launches
    Chrome with the persistent profile if needed)."""
    try:
        return open_url(url)
    except Exception as exc:
        return f"open_url failed: {type(exc).__name__}: {exc}"


@tool
def desktop_find(query: str) -> str:
    """Find a UI element by name on the :99 desktop via the accessibility
    tree (e.g. 'Sign in', 'Search'). Returns role, name and centre coords."""
    el = find_element(query)
    if not el:
        return f"not found: {query!r} (a11y tree had {len(a11y_tree(60))} elements)"
    return (f"{el['role']} {el['name']!r} at ({el['cx']}, {el['cy']}) "
            f"box={el['x']},{el['y']},{el['w']}x{el['h']} score={el['score']}")


@tool
def desktop_task(task: str, max_steps: int = 15, approved: bool = False) -> str:
    """Run a multi-step desktop task on Nexus's :99 desktop (screenshot →
    decide → act loop on the local brain). e.g. 'open github.com and
    search for langgraph'. Set approved=True to allow typing into
    password/billing fields."""
    from tools.desktop_agent import run_desktop_task  # noqa: PLC0415
    res = run_desktop_task(task, max_steps=max_steps, approved=approved)
    lines = [f"status={res.get('status')} steps={len(res.get('steps', []))}",
             f"summary: {res.get('summary')}",
             f"final_screenshot: {res.get('final_screenshot')}"]
    if res.get("needs_confirm"):
        lines.append(f"NEEDS_CONFIRM: {res['needs_confirm']} — re-run with approved=True")
    return "\n".join(lines)


DESKTOP_TOOLS = [desktop_screenshot, desktop_click, desktop_type,
                 desktop_open_url, desktop_find, desktop_task]
