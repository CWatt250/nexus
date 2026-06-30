"""Phase 21.2 — short-form video script writer.

Given a topic + duration, produce a scene-by-scene vertical-format
script with [VISUAL]: and [VOICEOVER]: blocks. Two backends:

1. Anthropic Claude (preferred, when ANTHROPIC_API_KEY is set in
   config/secrets.yaml) — cleaner prose, better at the casual brotha
   energy SOUL.md asks for.
2. Local Ollama qwen3.6 fallback — free, always available, used when
   the Anthropic key is missing. Same prompt template; quality is
   acceptable but slightly more boilerplate.

Output goes to content/scripts/YYYY-MM-DD_<slug>.md.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from core import secrets

log = logging.getLogger("nexus.script_writer")

ROOT = Path.home() / "AI_Agent"
SCRIPTS_DIR = ROOT / "content" / "scripts"
ENTITIES_DIR = ROOT / "wiki" / "entities"
SECONDS_PER_SCENE = 3.5  # spec says ~3-4s per scene; midpoint
ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"


def _live_model(key: str = "brain", default: str = "qwen3:8b") -> str:
    """Resolve from models.json (was hardcoded qwen3.6, pinned 23GB via
    keep_alive=-1). Resident brain = 0 extra VRAM."""
    try:
        return json.loads((Path.home() / "AI_Agent" / "models.json").read_text()).get(key) or default
    except Exception:
        return default


OLLAMA_MODEL = _live_model("brain")
MAX_WIKI_ENTITIES = 5  # cap injection to avoid prompt bloat
MAX_WIKI_CONTENT_CHARS = 4000  # per-entity cap; trim if longer


SYSTEM_PROMPT = """You are a short-form video scriptwriter for vertical-format (9:16) clips
intended for TikTok, Instagram Reels, and YouTube Shorts.

Constraints:
- Total duration is given in seconds; aim for ~3-4 seconds per scene.
- Each scene is a tight visual idea + 1-2 short voiceover sentences.
- Energetic, punchy, casual tone. Match the requested tone exactly.
- No narrator preamble. No "in this video" or "today we'll talk about" filler.
- Hook in the first 2 seconds — surprising claim, contrast, or question.
- End with a clear CTA or punchline.

Output strictly this format, no preamble or surrounding prose:

# <Title>

## Scene 1
[VISUAL]: <one short sentence describing the shot — subject, action, mood>
[VOICEOVER]: <1-2 short spoken sentences>

## Scene 2
[VISUAL]: ...
[VOICEOVER]: ...

(continue until total scenes covers the requested duration)
"""


@dataclass
class ScriptResult:
    path: str
    slug: str
    scene_count: int
    total_duration_seconds: float
    backend: str           # "anthropic" | "ollama"
    cost_usd: float        # 0.0 for ollama
    raw_text: str


# ── Wiki-aware context injection ───────────────────────────────────────
# Bare topics like "bidwatt" used to make qwen3.6 hallucinate (it
# invented a power device with cables and batteries). Same wiki-bypass
# pattern as Bug 6 (May 1) but in a new code path. Fix: scan the topic
# for known entity slugs and inject those wiki pages as authoritative
# context before the LLM call.

def find_wiki_entities(topic: str) -> list[dict]:
    """Scan the topic string for matches against wiki/entities/*.md
    filenames (case-insensitive substring match on the file stem).
    Returns up to MAX_WIKI_ENTITIES entries shaped
    {name, path, content}. Best-effort — never raises; returns []
    when wiki/entities/ is missing or unreadable.

    Matching is substring-based: "bidwatt" matches "BidWatt",
    "BIDWATT", "show me bidwatt", and "bidwatt-promo". Real-world
    entity slugs (colton, nexus, bidwatt, subwatt, argus) are
    distinctive enough that false positives are unlikely.
    """
    if not topic or not ENTITIES_DIR.exists():
        return []
    topic_lower = topic.lower()
    matches: list[dict] = []
    try:
        for entity_file in sorted(ENTITIES_DIR.glob("*.md")):
            slug = entity_file.stem.lower()
            if slug in topic_lower:
                try:
                    content = entity_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                if len(content) > MAX_WIKI_CONTENT_CHARS:
                    content = content[:MAX_WIKI_CONTENT_CHARS] + "\n\n[...truncated]"
                matches.append({
                    "name": slug,
                    "path": str(entity_file),
                    "content": content,
                })
                if len(matches) >= MAX_WIKI_ENTITIES:
                    break
    except OSError:
        return []
    return matches


def _compose_user_prompt(topic: str, duration_seconds: int, tone: str) -> tuple[str, list[str]]:
    """Build the user message both backends send to their LLM. Prepends
    a `## Wiki context` block when known entities are mentioned in the
    topic so the model writes from facts, not hallucination.

    Returns (user_prompt, matched_entity_names) — the second value is
    surfaced so callers can log / report which entities got injected.
    """
    target_scenes = max(3, round(duration_seconds / SECONDS_PER_SCENE))
    base_block = (
        f"Topic: {topic}\n"
        f"Total duration: {duration_seconds} seconds\n"
        f"Target scene count: {target_scenes} (~{SECONDS_PER_SCENE}s each)\n"
        f"Tone: {tone}\n\n"
        f"Write the script now. Output the markdown only, no extra commentary."
    )
    entities = find_wiki_entities(topic)
    if not entities:
        log.debug("script_writer: no wiki entities matched, using bare topic")
        return base_block, []

    names = [e["name"] for e in entities]
    log.info("script_writer: injecting wiki context for %s", names)

    sections = [
        "## Wiki context",
        "The user's topic mentions known entities. Use this authoritative info — do NOT invent facts about these. Quote details accurately.",
        "",
    ]
    for e in entities:
        sections.append(f"### {e['name']}")
        sections.append(e["content"].rstrip())
        sections.append("")
    sections.append("---")
    sections.append("")
    sections.append(base_block)
    return "\n".join(sections), names


# ── Anthropic backend ───────────────────────────────────────────────────
SONNET_INPUT_PER_M = 3.00
SONNET_OUTPUT_PER_M = 15.00


def _anthropic_generate(topic: str, duration_seconds: int, tone: str) -> tuple[str, float, dict]:
    """Returns (script_markdown, cost_usd, usage_dict). Raises on failure."""
    import anthropic  # noqa: PLC0415
    api_key = secrets.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    user_prompt, entities = _compose_user_prompt(topic, duration_seconds, tone)
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    in_tok = getattr(resp.usage, "input_tokens", 0) or 0
    out_tok = getattr(resp.usage, "output_tokens", 0) or 0
    cost = (in_tok / 1_000_000 * SONNET_INPUT_PER_M
            + out_tok / 1_000_000 * SONNET_OUTPUT_PER_M)
    return text, round(cost, 4), {
        "input_tokens": in_tok, "output_tokens": out_tok,
        "wiki_entities": entities,
    }


# ── Ollama fallback ────────────────────────────────────────────────────
def _ollama_generate(topic: str, duration_seconds: int, tone: str) -> tuple[str, float, dict]:
    """Returns (script_markdown, 0.0 cost, usage). Raises on failure."""
    import ollama  # noqa: PLC0415
    user_prompt, entities = _compose_user_prompt(topic, duration_seconds, tone)
    resp = ollama.Client(host="http://localhost:11434").chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0.7, "num_predict": 1500, "num_ctx": 8192},
        stream=False,
        think=False,
        keep_alive=-1,
    )
    text = (resp.get("message", {}) or {}).get("content", "") or ""
    # Strip any leaked <think> blocks the local model emits.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text, 0.0, {"wiki_entities": entities}


# ── Output parsing ─────────────────────────────────────────────────────
_SCENE_HEADING_RE = re.compile(r"^##\s+Scene\s+\d+\b", re.IGNORECASE | re.MULTILINE)


def _parse_scene_count(script_text: str) -> int:
    return len(_SCENE_HEADING_RE.findall(script_text))


def _slugify(topic: str, max_len: int = 50) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", topic).strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s[:max_len].rstrip("-") or "untitled"


def parse_scenes(script_text: str) -> list[dict]:
    """Pull scene blocks out of the markdown. Returns list of
    {scene_no, visual, voiceover}. Best-effort — skips scenes that
    don't have both fields."""
    scenes: list[dict] = []
    blocks = re.split(r"^##\s+Scene\s+(\d+)\b.*$", script_text, flags=re.MULTILINE)
    # blocks[0] is the prelude (title), then alternating (scene_no, body).
    i = 1
    while i + 1 < len(blocks):
        scene_no = int(blocks[i])
        body = blocks[i + 1]
        vm = re.search(r"\[VISUAL\]\s*:\s*(.+?)(?=\n\s*\[|\Z)", body, re.DOTALL | re.IGNORECASE)
        vo = re.search(r"\[VOICEOVER\]\s*:\s*(.+?)(?=\n\s*\[|\Z)", body, re.DOTALL | re.IGNORECASE)
        if vm and vo:
            scenes.append({
                "scene_no": scene_no,
                "visual": vm.group(1).strip(),
                "voiceover": vo.group(1).strip(),
            })
        i += 2
    return scenes


# ── Public entry points ────────────────────────────────────────────────
def script_write_core(
    topic: str,
    duration_seconds: int = 30,
    tone: str = "energetic",
) -> ScriptResult:
    """Generate, persist, and return a ScriptResult. Used by both the
    LangGraph tool wrapper and the content_create orchestrator.

    Anthropic primary, Ollama fallback. Raises on total failure."""
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    duration_seconds = max(5, min(int(duration_seconds), 600))
    last_error: Optional[Exception] = None
    backend = ""
    text = ""
    cost = 0.0
    if secrets.get("ANTHROPIC_API_KEY"):
        try:
            text, cost, _ = _anthropic_generate(topic, duration_seconds, tone)
            backend = "anthropic"
        except Exception as exc:  # falls through to local
            last_error = exc
    if not text:
        try:
            text, cost, _ = _ollama_generate(topic, duration_seconds, tone)
            backend = "ollama"
        except Exception as exc:
            raise RuntimeError(
                f"Both backends failed. Anthropic: {last_error}. Ollama: {exc}"
            ) from exc

    slug = _slugify(topic)
    out_path = SCRIPTS_DIR / f"{date.today().isoformat()}_{slug}.md"
    # Annotate the file with metadata so the content_create orchestrator
    # can pick up backend + cost when it stitches the final mp4.
    header = (
        f"<!--meta\n"
        f"{json.dumps({'backend': backend, 'cost_usd': cost, 'topic': topic, 'duration_seconds': duration_seconds, 'tone': tone}, ensure_ascii=False, indent=2)}\n"
        f"-->\n\n"
    )
    out_path.write_text(header + text, encoding="utf-8")

    scenes = parse_scenes(text)
    return ScriptResult(
        path=str(out_path),
        slug=slug,
        scene_count=len(scenes),
        total_duration_seconds=float(duration_seconds),
        backend=backend,
        cost_usd=cost,
        raw_text=text,
    )


@tool
def script_write(
    topic: str,
    duration_seconds: int = 30,
    tone: str = "energetic",
) -> str:
    """Generate a scene-by-scene short-form video script from a topic.

    Vertical-format (9:16, TikTok / Reels / Shorts). ~3-4 seconds per
    scene. Each scene has [VISUAL]: and [VOICEOVER]: blocks.

    Backend: Claude Sonnet 4.5 when ANTHROPIC_API_KEY is set, falls
    back to local qwen3.6 via Ollama. SLOW tier — Anthropic call costs
    real money (~$0.005-$0.02 per script depending on length).

    Args:
        topic: What the video is about. Be specific — "BidWatt promo
            for mechanical contractors" beats "construction app".
        duration_seconds: Target total duration. Default 30. Range 5-600.
        tone: One word ("energetic", "chill", "dramatic"). Default
            "energetic".

    Returns:
        Multi-line summary: file path, backend, cost, scene count.
    """
    result = script_write_core(topic, duration_seconds, tone)
    cost_str = f"${result.cost_usd:.4f}" if result.cost_usd else "free (local)"
    return (
        f"Script written: {result.path}\n"
        f"  backend: {result.backend} | cost: {cost_str}\n"
        f"  scenes : {result.scene_count} | target duration: {result.total_duration_seconds:.0f}s"
    )


SCRIPT_WRITER_TOOLS = [script_write]
