"""YouTube Transcript Tool for Nexus agent."""
from __future__ import annotations

import re
from typing import Optional

import requests
from langchain_core.tools import tool
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

OLLAMA_URL = "http://localhost:11434"


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # Maybe it's just the ID itself
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url
    return None


@tool
def youtube_transcript(url: str) -> str:
    """Extract full transcript from a YouTube video.

    Args:
        url: YouTube video URL or video ID

    Returns:
        Full transcript text or error message
    """
    video_id = extract_video_id(url)
    if not video_id:
        return f"Error: Could not extract video ID from URL: {url}"

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Try to get English transcript first, then any available
        transcript = None
        try:
            transcript = transcript_list.find_transcript(["en", "en-US", "en-GB"])
        except NoTranscriptFound:
            # Fall back to first available transcript
            for t in transcript_list:
                transcript = t
                break

        if transcript is None:
            return f"Error: No transcript available for video {video_id}"

        # Fetch the transcript
        transcript_data = transcript.fetch()

        # Combine all text segments
        full_text = " ".join(entry["text"] for entry in transcript_data)

        return f"Transcript for video {video_id}:\n\n{full_text}"

    except TranscriptsDisabled:
        return f"Error: Transcripts are disabled for video {video_id}"
    except VideoUnavailable:
        return f"Error: Video {video_id} is unavailable"
    except NoTranscriptFound:
        return f"Error: No transcript found for video {video_id}"
    except Exception as e:
        return f"Error extracting transcript: {type(e).__name__}: {e}"


@tool
def youtube_summary(url: str) -> str:
    """Extract transcript from YouTube video and summarize it using qwen3:4b.

    Args:
        url: YouTube video URL or video ID

    Returns:
        Summary of the video content or error message
    """
    # First get the transcript
    transcript_result = youtube_transcript.invoke(url)

    if transcript_result.startswith("Error:"):
        return transcript_result

    # Truncate if too long (qwen3:4b context limit consideration)
    transcript_text = transcript_result
    if len(transcript_text) > 15000:
        transcript_text = transcript_text[:15000] + "... [truncated]"

    # Summarize with qwen3:4b via Ollama
    prompt = f"""Summarize the following YouTube video transcript in 3-5 key points.
Focus on the main ideas, important details, and conclusions.

Transcript:
{transcript_text}

Summary:"""

    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": "qwen3:4b",
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 500},
            },
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        summary = result.get("response", "").strip()

        # Strip thinking tags if present (qwen3 sometimes outputs these)
        summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL).strip()

        return f"Video Summary:\n\n{summary}"

    except requests.exceptions.Timeout:
        return "Error: Summarization timed out"
    except requests.exceptions.RequestException as e:
        return f"Error calling Ollama for summarization: {e}"
    except Exception as e:
        return f"Error during summarization: {type(e).__name__}: {e}"


# Export for easy import
YOUTUBE_TOOLS = [youtube_transcript, youtube_summary]
