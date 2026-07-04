"""Real weather via wttr.in — free, no API key, single HTTP call.

Web-searching "weather today" returns whatever city the engine feels like
(Cardston AB, Lodi CA — both really happened, 2026-07-04). A dedicated
weather source is deterministic about location, so lite_agent can answer
"what's the weather" in one fast call.
"""
from __future__ import annotations

import httpx
from langchain_core.tools import tool

DEFAULT_LOCATION = "Pasco, WA"
_TIMEOUT = 10.0


def _fetch(location: str) -> dict:
    r = httpx.get(f"https://wttr.in/{location.replace(' ', '+')}",
                  params={"format": "j1"}, timeout=_TIMEOUT,
                  headers={"User-Agent": "curl/8"})
    r.raise_for_status()
    return r.json()


def weather_summary(location: str = "") -> str:
    """Plain-string current conditions + today/tomorrow for `location`."""
    loc = (location or "").strip() or DEFAULT_LOCATION
    data = _fetch(loc)
    cur = data["current_condition"][0]
    days = data.get("weather") or []
    area = ""
    try:
        a = data["nearest_area"][0]
        area = f"{a['areaName'][0]['value']}, {a['region'][0]['value']}"
    except (KeyError, IndexError):
        area = loc
    lines = [
        f"Weather in {area}: {cur['weatherDesc'][0]['value']}, "
        f"{cur['temp_F']}°F (feels like {cur['FeelsLikeF']}°F), "
        f"wind {cur['windspeedMiles']} mph, humidity {cur['humidity']}%."
    ]
    if days:
        lines.append(f"Today: high {days[0]['maxtempF']}°F / low {days[0]['mintempF']}°F.")
    if len(days) > 1:
        lines.append(f"Tomorrow: high {days[1]['maxtempF']}°F / low {days[1]['mintempF']}°F.")
    return " ".join(lines)


@tool
def get_weather(location: str = "") -> str:
    """Current weather + today/tomorrow forecast for a location.

    Args:
        location: City/place (e.g. "Pasco, WA", "Seattle"). Empty = the
            user's home area.

    Returns:
        One-paragraph weather summary (conditions, temp, wind, hi/lo).
    """
    try:
        return weather_summary(location)
    except Exception as exc:
        return f"couldn't fetch weather for {location or DEFAULT_LOCATION}: {type(exc).__name__}: {exc}"


WEATHER_TOOLS = [get_weather]
