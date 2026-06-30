"""Computer Use Tool for Nexus agent — mouse, keyboard, and screen control."""
from __future__ import annotations

import base64
import io
import os
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

from langchain_core.tools import tool
from PIL import Image

# Lazy load pyautogui to avoid display errors on import
_pyautogui = None

# Polish #7 — virtual display fallback. When the host has no monitor
# attached (DISPLAY unset, or the real X session has gone away), Nexus
# can still screenshot via the nexus-xvfb.service-managed Xvfb instance
# at :99. Set up by SUDO_DISPATCH.sh.
_HEADLESS_DISPLAY = ":99"


def _ensure_display_env() -> Optional[str]:
    """Return the DISPLAY value to use for pyautogui. If the env var is
    unset and the headless Xvfb at :99 is reachable, return ':99' (and
    set os.environ['DISPLAY']). Otherwise return whatever the env says.

    Caller should call this BEFORE first pyautogui access — pyautogui
    reads $DISPLAY at import time."""
    current = os.environ.get("DISPLAY")
    if current:
        return current
    # No display attached. Probe :99 — if Xvfb is up, xdpyinfo answers.
    try:
        proc = subprocess.run(
            ["xdpyinfo", "-display", _HEADLESS_DISPLAY],
            capture_output=True, timeout=2,
        )
        if proc.returncode == 0:
            os.environ["DISPLAY"] = _HEADLESS_DISPLAY
            return _HEADLESS_DISPLAY
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _get_pyautogui():
    """Lazy load pyautogui with proper error handling. Falls back to the
    Xvfb virtual display at :99 when no real DISPLAY is set."""
    global _pyautogui
    if _pyautogui is None:
        display = _ensure_display_env()
        if not display:
            raise RuntimeError(
                "No DISPLAY available. Start the headless Xvfb service "
                "(`sudo systemctl start nexus-xvfb`) or run "
                "SUDO_DISPATCH.sh to install it."
            )
        try:
            import pyautogui
            pyautogui.FAILSAFE = True  # Move mouse to corner to abort
            pyautogui.PAUSE = 0.1  # Small pause between actions
            _pyautogui = pyautogui
        except Exception as e:
            raise RuntimeError(
                f"Cannot initialize pyautogui on DISPLAY={display}: {e}"
            )
    return _pyautogui

# Output directory for screenshots
OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Dangerous key combinations that require extra confirmation
DANGEROUS_KEYS = {"delete", "backspace", "ctrl+a", "ctrl+shift+del", "alt+f4"}


@tool
def mouse_move(x: int, y: int) -> str:
    """Move the mouse cursor to the specified screen coordinates.

    Args:
        x: X coordinate (pixels from left)
        y: Y coordinate (pixels from top)

    Returns:
        Success message with final position
    """
    try:
        # Get screen size for validation
        screen_w, screen_h = _get_pyautogui().size()
        if x < 0 or x > screen_w or y < 0 or y > screen_h:
            return f"Error: Coordinates ({x}, {y}) out of screen bounds ({screen_w}x{screen_h})"

        _get_pyautogui().moveTo(x, y, duration=0.2)
        return f"Mouse moved to ({x}, {y})"
    except Exception as e:
        return f"Error moving mouse: {type(e).__name__}: {e}"


@tool
def mouse_click(x: int, y: int, button: str = "left", approve: bool = False) -> str:
    """Click the mouse at the specified coordinates.

    Phase 16.2 safety: clicks land only when the active window title
    matches the approved-app whitelist (firefox/chrome/code/terminals/
    file managers/nexus). Outside that list, the click is refused unless
    `approve=True` is set explicitly.

    Args:
        x: X coordinate (pixels from left)
        y: Y coordinate (pixels from top)
        button: Mouse button - "left", "right", or "middle"
        approve: bypass the active-window safety gate (model must opt in).
    """
    if button not in ("left", "right", "middle"):
        return f"Error: Invalid button '{button}'. Use 'left', 'right', or 'middle'"
    try:
        screen_w, screen_h = _get_pyautogui().size()
    except Exception as e:
        return f"Error reading screen size: {type(e).__name__}: {e}"
    if x < 0 or x > screen_w or y < 0 or y > screen_h:
        return f"Error: Coordinates ({x}, {y}) out of screen bounds ({screen_w}x{screen_h})"
    return _click_safe(x, y, button=button, approve=approve)


@tool
def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: str = "left") -> str:
    """Drag the mouse from one position to another.

    Args:
        x1: Starting X coordinate
        y1: Starting Y coordinate
        x2: Ending X coordinate
        y2: Ending Y coordinate
        button: Mouse button to hold during drag

    Returns:
        Success message or error
    """
    try:
        _get_pyautogui().moveTo(x1, y1, duration=0.1)
        _get_pyautogui().drag(x2 - x1, y2 - y1, duration=0.3, button=button)
        return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})"
    except Exception as e:
        return f"Error dragging: {type(e).__name__}: {e}"


@tool
def keyboard_type(text: str) -> str:
    """Type text using the keyboard.

    Args:
        text: Text to type

    Returns:
        Success message or error
    """
    if len(text) > 1000:
        return "Error: Text too long (max 1000 chars). Break into smaller chunks."

    try:
        _get_pyautogui().typewrite(text, interval=0.02)
        return f"Typed {len(text)} characters"
    except Exception as e:
        return f"Error typing: {type(e).__name__}: {e}"


@tool
def keyboard_press(key: str) -> str:
    """Press a keyboard key or key combination.

    Args:
        key: Key to press (e.g., "enter", "escape", "ctrl+c", "alt+tab")

    Returns:
        Success message or error
    """
    key_lower = key.lower()

    # Safety check for dangerous keys
    if key_lower in DANGEROUS_KEYS:
        return f"SAFETY: Key '{key}' is potentially destructive. Please confirm this action explicitly."

    try:
        if "+" in key:
            # Handle key combinations like ctrl+c, alt+tab
            keys = [k.strip() for k in key.split("+")]
            _get_pyautogui().hotkey(*keys)
        else:
            _get_pyautogui().press(key)
        return f"Pressed key: {key}"
    except Exception as e:
        return f"Error pressing key: {type(e).__name__}: {e}"


@tool
def screenshot(save_to_file: bool = True) -> str:
    """Take a screenshot of the current screen.

    Args:
        save_to_file: If True, saves to file and returns path. If False, returns base64.

    Returns:
        File path or base64 encoded image
    """
    try:
        img = _get_pyautogui().screenshot()

        if save_to_file:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filepath = OUTPUT_DIR / f"screenshot_{timestamp}.png"
            img.save(filepath)
            return f"Screenshot saved: {filepath}"
        else:
            # Return base64 for API use
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode()
            return f"data:image/png;base64,{b64[:100]}... (truncated, {len(b64)} chars)"

    except Exception as e:
        return f"Error taking screenshot: {type(e).__name__}: {e}"


@tool
def find_on_screen(image_path: str) -> str:
    """Find an image on the screen and return its coordinates.

    Args:
        image_path: Path to the image file to find on screen

    Returns:
        Coordinates of the image center, or error if not found
    """
    path = Path(image_path)
    if not path.exists():
        return f"Error: Image file not found: {image_path}"

    try:
        location = _get_pyautogui().locateCenterOnScreen(str(path), confidence=0.8)
        if location:
            return f"Found at coordinates: ({location.x}, {location.y})"
        else:
            return "Image not found on screen"
    except Exception as e:
        return f"Error finding image: {type(e).__name__}: {e}"


@tool
def find_on_screen_vision(description: str, vision_model: str = "qwen2.5vl:7b") -> str:
    """Locate a UI element on screen by *natural-language description* using
    a vision-language model (Phase 16.2).

    Falls back gracefully when no VL model is installed: returns a hint to
    pull qwen2.5vl:7b (~6GB) so the operator can opt in. The model reads a
    fresh full-screen screenshot and replies with `(x, y)` coordinates.

    Args:
        description: e.g. "the blue 'Save' button in the toolbar".
        vision_model: Ollama VL model name (default qwen2.5vl:7b).
    """
    import base64
    import io
    import re
    try:
        import ollama
    except Exception as exc:
        return f"vision unavailable: {type(exc).__name__}: {exc}"

    pyautogui = _get_pyautogui()
    img = pyautogui.screenshot()
    width, height = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    prompt = (
        f"Image is {width}x{height} px. Locate: {description.strip()}\n"
        "Reply with exactly one line in the format `x,y` (integers, top-left "
        "origin) at the *centre* of the element. If you can't find it, reply "
        "with the single word: NOTFOUND."
    )
    try:
        resp = ollama.Client(host="http://localhost:11434").chat(
            model=vision_model,
            messages=[{"role": "user", "content": prompt, "images": [img_b64]}],
            stream=False,
            think=False,
            # num_gpu=0 pins the VL model to CPU so it can't evict/OOM the
            # resident brain — matches tools/vision_tool._vision_chat. This
            # call was the one VL path missing the pin.
            options={"temperature": 0.0, "num_predict": 16, "num_ctx": 4096,
                     "num_gpu": 0},
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "no such model" in msg or "could not" in msg:
            return (
                f"vision model {vision_model!r} not installed. "
                f"Run: ollama pull {vision_model}\n"
                "Falling back: use find_on_screen(image_path) with a template image."
            )
        return f"vision call failed: {type(exc).__name__}: {exc}"
    content = ""
    if isinstance(resp, dict):
        content = ((resp.get("message") or {}).get("content") or "").strip()
    else:
        m = getattr(resp, "message", None)
        content = (getattr(m, "content", "") or "").strip()
    if "NOTFOUND" in content.upper():
        return f"not found on screen ({content!r})"
    m = re.search(r"(-?\d+)\s*[,\s]\s*(-?\d+)", content)
    if not m:
        return f"could not parse coords from VL reply: {content!r}"
    x, y = int(m.group(1)), int(m.group(2))
    if not (0 <= x < width and 0 <= y < height):
        return f"VL coords ({x},{y}) out of {width}x{height} screen"
    return f"Found at coordinates: ({x}, {y})"


# Phase 16.2 — clicks outside one of these window titles need an explicit
# approval flag from the model. Lower-cased, partial-match.
APPROVED_WINDOW_TITLES = {"firefox", "chromium", "chrome", "code", "vscodium",
                          "terminal", "konsole", "xterm", "gnome-terminal",
                          "nautilus", "thunar", "dolphin", "nexus"}


def _active_window_title() -> str:
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _click_safe(x: int, y: int, *, button: str, approve: bool) -> str:
    title = _active_window_title()
    title_lower = title.lower()
    matches = any(t in title_lower for t in APPROVED_WINDOW_TITLES)
    if not matches and not approve:
        return (
            f"SAFETY: active window {title!r} is not in the approved list "
            f"({sorted(APPROVED_WINDOW_TITLES)}). Re-call mouse_click with "
            f"approve=True to override."
        )
    pyautogui = _get_pyautogui()
    try:
        pyautogui.click(x, y, button=button)
        return f"Clicked at ({x}, {y}) with {button} button"
    except Exception as e:
        return f"Error clicking: {type(e).__name__}: {e}"


@tool
def open_app(name: str) -> str:
    """Open an application by name (Linux only).

    Args:
        name: Application name (e.g., "firefox", "code", "nautilus")

    Returns:
        Success message or error
    """
    # Whitelist of safe applications
    SAFE_APPS = {
        "firefox", "chromium", "chrome", "google-chrome",
        "code", "codium", "gedit", "kate", "vim", "nvim",
        "nautilus", "dolphin", "thunar", "nemo",
        "terminal", "gnome-terminal", "konsole", "xterm",
        "gimp", "inkscape", "libreoffice",
        "vlc", "mpv", "totem",
        "slack", "discord", "telegram-desktop",
    }

    app_lower = name.lower()
    if app_lower not in SAFE_APPS:
        return f"SAFETY: Application '{name}' not in whitelist. Safe apps: {', '.join(sorted(SAFE_APPS))}"

    try:
        subprocess.Popen(
            [name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Launched application: {name}"
    except FileNotFoundError:
        return f"Error: Application '{name}' not found"
    except Exception as e:
        return f"Error launching app: {type(e).__name__}: {e}"


@tool
def get_screen_size() -> str:
    """Get the current screen resolution.

    Returns:
        Screen dimensions as "WIDTHxHEIGHT"
    """
    try:
        width, height = _get_pyautogui().size()
        return f"Screen size: {width}x{height}"
    except Exception as e:
        return f"Error getting screen size: {type(e).__name__}: {e}"


@tool
def get_mouse_position() -> str:
    """Get the current mouse cursor position.

    Returns:
        Current mouse coordinates
    """
    try:
        x, y = _get_pyautogui().position()
        return f"Mouse position: ({x}, {y})"
    except Exception as e:
        return f"Error getting mouse position: {type(e).__name__}: {e}"


# Export all tools
COMPUTER_USE_TOOLS = [
    mouse_move,
    mouse_click,
    mouse_drag,
    keyboard_type,
    keyboard_press,
    screenshot,
    find_on_screen,
    find_on_screen_vision,
    open_app,
    get_screen_size,
    get_mouse_position,
]
