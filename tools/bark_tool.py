"""Bark Voice Acting Tool for Nexus agent — character voice generation."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "audio" / "voices"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Lazy-load Bark
_bark_available = None

# Available voice presets
VOICE_PRESETS = {
    "narrator": "v2/en_speaker_0",
    "hero": "v2/en_speaker_1",
    "villain": "v2/en_speaker_2",
    "child": "v2/en_speaker_3",
    "elder": "v2/en_speaker_4",
    "female_1": "v2/en_speaker_5",
    "female_2": "v2/en_speaker_6",
    "male_1": "v2/en_speaker_7",
    "male_2": "v2/en_speaker_8",
    "announcer": "v2/en_speaker_9",
}


def _check_bark():
    """Check if Bark is available."""
    global _bark_available

    if _bark_available is not None:
        return _bark_available

    try:
        from bark import SAMPLE_RATE, generate_audio, preload_models
        preload_models()
        _bark_available = True
        return True
    except ImportError:
        _bark_available = False
        return False
    except Exception as e:
        _bark_available = False
        print(f"Bark init error: {e}")
        return False


@tool
def bark_speak(text: str, voice_preset: str = "narrator", filename: Optional[str] = None) -> str:
    """Generate voice acting audio from text using Bark.

    Args:
        text: The text to speak (max 200 chars for best quality)
        voice_preset: Voice to use - narrator, hero, villain, child, elder,
                     female_1, female_2, male_1, male_2, announcer
                     Or use raw preset like "v2/en_speaker_3"
        filename: Optional filename (without extension)

    Returns:
        Path to saved audio file or error message
    """
    if not _check_bark():
        return (
            "Error: Bark is not installed.\n"
            "Install with: pip install bark\n"
            "Note: First run downloads ~5GB of models.\n"
            "Requires GPU for reasonable performance."
        )

    # Map friendly names to presets
    actual_preset = VOICE_PRESETS.get(voice_preset.lower(), voice_preset)

    # Truncate text if too long
    if len(text) > 200:
        text = text[:200]

    try:
        from bark import SAMPLE_RATE, generate_audio
        import scipy.io.wavfile

        # Generate audio
        audio_array = generate_audio(text, history_prompt=actual_preset)

        # Generate filename
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_text = "".join(c if c.isalnum() else "_" for c in text[:20])
            filename = f"voice_{timestamp}_{voice_preset}_{safe_text}"

        output_path = OUTPUT_DIR / f"{filename}.wav"

        # Save audio
        scipy.io.wavfile.write(str(output_path), rate=SAMPLE_RATE, data=audio_array)

        return f"Voice audio saved: {output_path}"

    except Exception as e:
        return f"Error generating voice: {type(e).__name__}: {e}"


@tool
def bark_list_presets() -> str:
    """List available voice presets for Bark.

    Returns:
        List of available voice presets
    """
    result = "Available voice presets:\n"
    for name, preset in VOICE_PRESETS.items():
        result += f"  - {name}: {preset}\n"
    result += "\nYou can also use raw presets like 'v2/en_speaker_0' through 'v2/en_speaker_9'"
    return result


@tool
def bark_list_voices(limit: int = 20) -> str:
    """List recently generated voice audio files.

    Args:
        limit: Maximum number of files to list

    Returns:
        List of voice files
    """
    try:
        files = sorted(
            OUTPUT_DIR.glob("*.wav"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not files:
            return "No voice audio generated yet."

        result = f"Recent voice files ({min(len(files), limit)} of {len(files)}):\n"
        for f in files[:limit]:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            size_kb = f.stat().st_size // 1024
            result += f"  {mtime} - {f.name} ({size_kb}KB)\n"

        return result

    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# Export tools
BARK_TOOLS = [bark_speak, bark_list_presets, bark_list_voices]
