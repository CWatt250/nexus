"""Audio Generation Tool for Nexus agent — generates SFX and music using AudioCraft."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "audio"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Lazy-load AudioCraft to avoid import errors
_audiocraft_available = None
_audio_gen = None
_music_gen = None


def _check_audiocraft():
    """Check if AudioCraft is available and initialize generators."""
    global _audiocraft_available, _audio_gen, _music_gen

    if _audiocraft_available is not None:
        return _audiocraft_available

    try:
        from audiocraft.models import AudioGen, MusicGen
        _audio_gen = AudioGen.get_pretrained("facebook/audiogen-medium")
        _music_gen = MusicGen.get_pretrained("facebook/musicgen-small")
        _audiocraft_available = True
        return True
    except ImportError:
        _audiocraft_available = False
        return False
    except Exception as e:
        _audiocraft_available = False
        print(f"AudioCraft init error: {e}")
        return False


@tool
def generate_sfx(prompt: str, duration: float = 3.0, filename: Optional[str] = None) -> str:
    """Generate a sound effect from a text prompt.

    Args:
        prompt: Description of the sound (e.g., "explosion", "footsteps on gravel")
        duration: Duration in seconds (max 10)
        filename: Optional filename (without extension)

    Returns:
        Path to saved audio file or error message
    """
    if not _check_audiocraft():
        return (
            "Error: AudioCraft is not installed.\n"
            "Install with: pip install audiocraft\n"
            "Note: Requires PyTorch and significant disk space (~2GB models)"
        )

    if duration > 10:
        duration = 10.0

    try:
        global _audio_gen
        _audio_gen.set_generation_params(duration=duration)
        wav = _audio_gen.generate([prompt])

        # Generate filename
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt[:20])
            filename = f"sfx_{timestamp}_{safe_prompt}"

        output_path = OUTPUT_DIR / f"{filename}.wav"

        # Save audio
        import torchaudio
        torchaudio.save(str(output_path), wav[0].cpu(), sample_rate=_audio_gen.sample_rate)

        return f"Sound effect saved: {output_path}"

    except Exception as e:
        return f"Error generating SFX: {type(e).__name__}: {e}"


@tool
def generate_music(prompt: str, duration: float = 10.0, filename: Optional[str] = None) -> str:
    """Generate background music from a text prompt.

    Args:
        prompt: Description of the music (e.g., "upbeat electronic game music")
        duration: Duration in seconds (max 30)
        filename: Optional filename (without extension)

    Returns:
        Path to saved audio file or error message
    """
    if not _check_audiocraft():
        return (
            "Error: AudioCraft is not installed.\n"
            "Install with: pip install audiocraft\n"
            "Note: Requires PyTorch and significant disk space (~2GB models)"
        )

    if duration > 30:
        duration = 30.0

    try:
        global _music_gen
        _music_gen.set_generation_params(duration=duration)
        wav = _music_gen.generate([prompt])

        # Generate filename
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt[:20])
            filename = f"music_{timestamp}_{safe_prompt}"

        output_path = OUTPUT_DIR / f"{filename}.wav"

        # Save audio
        import torchaudio
        torchaudio.save(str(output_path), wav[0].cpu(), sample_rate=_music_gen.sample_rate)

        return f"Music saved: {output_path}"

    except Exception as e:
        return f"Error generating music: {type(e).__name__}: {e}"


@tool
def list_audio_files(limit: int = 20) -> str:
    """List recently generated audio files.

    Args:
        limit: Maximum number of files to list

    Returns:
        List of audio files with timestamps
    """
    try:
        files = sorted(
            list(OUTPUT_DIR.glob("*.wav")) + list(OUTPUT_DIR.glob("*.mp3")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not files:
            return "No audio files generated yet."

        result = f"Recent audio files ({min(len(files), limit)} of {len(files)}):\n"
        for f in files[:limit]:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            size_kb = f.stat().st_size // 1024
            result += f"  {mtime} - {f.name} ({size_kb}KB)\n"

        return result

    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# Export tools
AUDIO_GEN_TOOLS = [generate_sfx, generate_music, list_audio_files]
