"""Computer Use Tool for Nexus agent — mouse, keyboard, and screen control."""
from __future__ import annotations

import base64
import io
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

from langchain_core.tools import tool
from PIL import Image

# Lazy load pyautogui to avoid display errors on import
_pyautogui = None


def _get_pyautogui():
    """Lazy load pyautogui with proper error handling."""
    global _pyautogui
    if _pyautogui is None:
        try:
            import pyautogui
            pyautogui.FAILSAFE = True  # Move mouse to corner to abort
            pyautogui.PAUSE = 0.1  # Small pause between actions
            _pyautogui = pyautogui
        except Exception as e:
            raise RuntimeError(f"Cannot initialize pyautogui (display issue?): {e}")
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
def mouse_click(x: int, y: int, button: str = "left") -> str:
    """Click the mouse at the specified coordinates.

    Args:
        x: X coordinate (pixels from left)
        y: Y coordinate (pixels from top)
        button: Mouse button - "left", "right", or "middle"

    Returns:
        Success message or error
    """
    if button not in ("left", "right", "middle"):
        return f"Error: Invalid button '{button}'. Use 'left', 'right', or 'middle'"

    try:
        screen_w, screen_h = _get_pyautogui().size()
        if x < 0 or x > screen_w or y < 0 or y > screen_h:
            return f"Error: Coordinates ({x}, {y}) out of screen bounds ({screen_w}x{screen_h})"

        _get_pyautogui().click(x, y, button=button)
        return f"Clicked {button} button at ({x}, {y})"
    except Exception as e:
        return f"Error clicking: {type(e).__name__}: {e}"


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
    open_app,
    get_screen_size,
    get_mouse_position,
]
