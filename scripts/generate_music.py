"""One-shot procedural music generator for Phase 21.2 Part 2.1.

Produces five 60-second mp3 tracks — one per tone bucket. These are
royalty-free by construction (sine/saw additive synthesis, no samples).
Sound quality is intentionally background-grade; tracks are mixed at
0.15 under voiceover so they only need to set mood.

Run once:
    ~/AI_Agent/venv/bin/python content/music/_generate_tracks.py
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from scipy.io import wavfile

SR = 44100  # sample rate
HERE = Path(__file__).parent

# Note frequencies (Hz). MIDI A4 = 440.
NOTE = {
    "C2": 65.41, "E2": 82.41, "G2": 98.00, "A2": 110.00,
    "C3": 130.81, "D3": 146.83, "E3": 164.81, "F3": 174.61,
    "G3": 196.00, "A3": 220.00, "B3": 246.94,
    "C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23,
    "G4": 392.00, "A4": 440.00, "B4": 493.88, "C5": 523.25,
    "D5": 587.33, "E5": 659.25, "G5": 783.99,
}


def adsr(n: int, a: float = 0.05, d: float = 0.1, s: float = 0.7, r: float = 0.2) -> np.ndarray:
    """Attack/Decay/Sustain/Release envelope, fractional of total length."""
    env = np.ones(n)
    a_n, d_n, r_n = int(n * a), int(n * d), int(n * r)
    if a_n:
        env[:a_n] = np.linspace(0, 1, a_n)
    if d_n:
        env[a_n : a_n + d_n] = np.linspace(1, s, d_n)
    if r_n:
        env[-r_n:] *= np.linspace(1, 0, r_n)
    if a_n + d_n < n - r_n:
        env[a_n + d_n : n - r_n] = s
    return env


def sine(freq: float, dur: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return np.sin(2 * np.pi * freq * t)


def saw(freq: float, dur: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return 2 * (t * freq - np.floor(0.5 + t * freq))


def triangle(freq: float, dur: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return 2 * np.abs(2 * (t * freq - np.floor(0.5 + t * freq))) - 1


def soft_kick(dur: float = 0.25, sr: int = SR) -> np.ndarray:
    """Pitched-down sine — usable as a soft kick drum."""
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    freq = 120 * np.exp(-t * 12) + 40
    sig = np.sin(2 * np.pi * np.cumsum(freq) / sr)
    env = np.exp(-t * 8)
    return sig * env * 0.6


def soft_hat(dur: float = 0.05, sr: int = SR) -> np.ndarray:
    """Filtered noise burst as hi-hat."""
    n = int(sr * dur)
    noise = np.random.uniform(-1, 1, n)
    env = np.exp(-np.linspace(0, dur, n) * 60)
    return noise * env * 0.15


def chord(notes: list[str], dur: float, voices: tuple = ("sine",)) -> np.ndarray:
    """Stack notes into a chord with given waveforms."""
    n = int(SR * dur)
    out = np.zeros(n)
    for note in notes:
        f = NOTE[note]
        for v in voices:
            if v == "sine":
                out += sine(f, dur)
            elif v == "saw":
                out += saw(f, dur) * 0.4
            elif v == "tri":
                out += triangle(f, dur) * 0.6
    return out * adsr(n, a=0.2, d=0.1, s=0.8, r=0.3) / max(1, len(notes) * len(voices))


def normalize(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = np.max(np.abs(x))
    if m == 0:
        return x
    return x * (peak / m)


def write_mp3(samples: np.ndarray, path: Path) -> None:
    """Write float32 [-1,1] samples → mp3 via ffmpeg (lame).

    Goes via a temp wav so we use ffmpeg directly (no extra deps)."""
    samples = np.clip(samples, -1.0, 1.0).astype(np.float32)
    pcm = (samples * 32767).astype(np.int16)
    tmp_wav = path.with_suffix(".tmp.wav")
    wavfile.write(str(tmp_wav), SR, pcm)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(tmp_wav), "-codec:a", "libmp3lame", "-b:a", "128k",
         str(path)],
        check=True,
    )
    tmp_wav.unlink()


# ── Track generators ───────────────────────────────────────────────────

def gen_chill(duration: float = 60.0) -> np.ndarray:
    """Slow ambient pad. C-Am-F-G progression, 4 bars at ~60 BPM."""
    bar = duration / 4
    progression = [
        ["C3", "E3", "G3", "C4"],
        ["A2", "C3", "E3", "A3"],
        ["F3", "A3", "C4", "F4"],
        ["G3", "B3", "D4", "G4"],
    ]
    out = np.concatenate([chord(c, bar, voices=("sine", "tri")) for c in progression])
    # subtle high shimmer
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    shimmer = 0.05 * np.sin(2 * np.pi * NOTE["C5"] * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.3 * t))
    return normalize(out + shimmer[:len(out)], peak=0.7)


def gen_energetic(duration: float = 60.0) -> np.ndarray:
    """Driving 120 BPM with kick + hat + bassline."""
    bpm = 120
    beat = 60.0 / bpm  # 0.5s
    n_beats = int(duration / beat)
    n_total = int(SR * duration)
    out = np.zeros(n_total)

    # Kick on every beat, hi-hat on offbeat
    for i in range(n_beats):
        idx = int(i * beat * SR)
        k = soft_kick(0.25)
        end = min(idx + len(k), n_total)
        out[idx:end] += k[:end - idx]
        if i % 2 == 1:
            h = soft_hat(0.08)
            end_h = min(idx + len(h), n_total)
            out[idx:end_h] += h[:end_h - idx]

    # Bass: C E G E pattern, 8 beats per phrase
    bass_notes = ["C2", "E2", "G2", "E2"] * (n_beats // 4 + 1)
    for i in range(n_beats):
        idx = int(i * beat * SR)
        b = saw(NOTE[bass_notes[i]], beat) * 0.3
        b *= adsr(len(b), a=0.01, d=0.2, s=0.5, r=0.1)
        end = min(idx + len(b), n_total)
        out[idx:end] += b[:end - idx]

    # Pad on top
    bar = duration / 4
    chords = [["C3", "E3", "G3"], ["G2", "B3", "D4"],
              ["A2", "C3", "E3"], ["F3", "A3", "C4"]]
    pad = np.concatenate([chord(c, bar, voices=("sine",)) * 0.3 for c in chords])
    out[:len(pad)] += pad

    return normalize(out, peak=0.85)


def gen_dramatic(duration: float = 60.0) -> np.ndarray:
    """Low drone with rising swells."""
    n_total = int(SR * duration)
    t = np.linspace(0, duration, n_total, endpoint=False)
    # Sustained low drone (C2 + G2 fifth)
    drone = 0.4 * np.sin(2 * np.pi * NOTE["C2"] * t) + 0.3 * np.sin(2 * np.pi * NOTE["G2"] * t)
    # Slow tremolo
    drone *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.2 * t)
    # Three swells at 15s, 30s, 45s
    swells = np.zeros(n_total)
    for center in (15, 30, 45):
        sw_start = int(center * SR) - SR * 2
        sw_end = int(center * SR) + SR * 3
        if sw_start < 0 or sw_end > n_total:
            continue
        sw_t = np.linspace(0, 5, sw_end - sw_start)
        env = np.exp(-((sw_t - 2.5) ** 2) / 1.5)
        swell = np.sin(2 * np.pi * NOTE["C3"] * sw_t) * env * 0.5
        swells[sw_start:sw_end] += swell
    return normalize(drone + swells, peak=0.8)


def gen_cinematic(duration: float = 60.0) -> np.ndarray:
    """Sweeping orchestral-feel saw chords with rise."""
    bar = duration / 4
    # Em - C - G - D progression, classic epic
    progression = [
        ["E3", "G3", "B3", "E4"],
        ["C3", "E3", "G3", "C4"],
        ["G3", "B3", "D4", "G4"],
        ["D3", "F3", "A3", "D4"],
    ]
    out = np.concatenate([chord(c, bar, voices=("saw", "tri")) for c in progression])
    # Rising bass over time
    t = np.linspace(0, duration, len(out), endpoint=False)
    bass_freq = NOTE["E2"] * (1 + t / duration * 0.5)
    bass = 0.3 * np.sin(2 * np.pi * np.cumsum(bass_freq) / SR)
    return normalize(out + bass, peak=0.9)


def gen_minimal(duration: float = 60.0) -> np.ndarray:
    """Sparse sine pad. Two notes oscillating slowly."""
    n_total = int(SR * duration)
    t = np.linspace(0, duration, n_total, endpoint=False)
    a = 0.4 * np.sin(2 * np.pi * NOTE["C4"] * t)
    b = 0.4 * np.sin(2 * np.pi * NOTE["G3"] * t)
    # Crossfade between a and b every 4 seconds
    mix = 0.5 + 0.5 * np.sin(2 * np.pi * 0.125 * t)
    out = a * mix + b * (1 - mix)
    out *= 0.6 + 0.2 * np.sin(2 * np.pi * 0.05 * t)
    return normalize(out, peak=0.5)


GENERATORS = {
    "chill/chill_01.mp3": gen_chill,
    "energetic/energetic_01.mp3": gen_energetic,
    "dramatic/dramatic_01.mp3": gen_dramatic,
    "cinematic/cinematic_01.mp3": gen_cinematic,
    "minimal/minimal_01.mp3": gen_minimal,
}


def main() -> None:
    np.random.seed(42)
    for rel, fn in GENERATORS.items():
        out_path = HERE / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            print(f"skip (exists): {rel}")
            continue
        print(f"generating: {rel}")
        samples = fn(60.0)
        write_mp3(samples, out_path)
        print(f"  → {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
