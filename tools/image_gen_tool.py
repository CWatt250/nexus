"""Image Generation Tool for Nexus agent — using ERNIE API or fallback."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

# Load environment
load_dotenv(Path.home() / "AI_Agent" / ".env")

ERNIE_API_KEY = os.getenv("ERNIE_API_KEY", "")
OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ERNIE API endpoint (placeholder - update with actual endpoint)
ERNIE_API_URL = "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/text2image"


@tool
def generate_image(
    prompt: str,
    size: str = "1024x1024",
    style: str = "realistic",
    filename: Optional[str] = None,
) -> str:
    """Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate
        size: Image size - "512x512", "1024x1024", or "1792x1024"
        style: Image style - "realistic", "artistic", "cartoon", "anime"
        filename: Optional filename (without extension). Auto-generated if not provided.

    Returns:
        Path to saved image or error message
    """
    if not ERNIE_API_KEY:
        return (
            "Error: ERNIE_API_KEY not configured.\n"
            "Add ERNIE_API_KEY=your_key to ~/AI_Agent/.env\n\n"
            "Alternative: Use local Stable Diffusion via Ollama when available:\n"
            "  ollama run stable-diffusion\n"
            "Or use the Replicate API with a free tier."
        )

    # Validate size
    valid_sizes = {"512x512", "1024x1024", "1792x1024"}
    if size not in valid_sizes:
        return f"Error: Invalid size '{size}'. Valid sizes: {', '.join(valid_sizes)}"

    # Parse size
    width, height = map(int, size.split("x"))

    try:
        # Make API request to ERNIE
        with httpx.Client(timeout=120) as client:
            response = client.post(
                f"{ERNIE_API_URL}?access_token={ERNIE_API_KEY}",
                json={
                    "prompt": prompt,
                    "style": style,
                    "width": width,
                    "height": height,
                },
            )
            response.raise_for_status()
            data = response.json()

        # Check for errors in response
        if "error_code" in data:
            return f"ERNIE API error: {data.get('error_msg', 'Unknown error')}"

        # Get image URL or base64 from response
        image_url = data.get("data", {}).get("image_url")
        image_b64 = data.get("data", {}).get("image")

        if not image_url and not image_b64:
            return "Error: No image returned from ERNIE API"

        # Generate filename if not provided
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt[:30])
            filename = f"{timestamp}_{safe_prompt}"

        filepath = OUTPUT_DIR / f"{filename}.png"

        # Download or decode the image
        if image_url:
            with httpx.Client(timeout=60) as client:
                img_response = client.get(image_url)
                img_response.raise_for_status()
                filepath.write_bytes(img_response.content)
        elif image_b64:
            import base64
            img_data = base64.b64decode(image_b64)
            filepath.write_bytes(img_data)

        return f"Image saved: {filepath}"

    except httpx.TimeoutException:
        return "Error: Image generation timed out"
    except httpx.HTTPError as e:
        return f"Error calling ERNIE API: {e}"
    except Exception as e:
        return f"Error generating image: {type(e).__name__}: {e}"


@tool
def list_generated_images(limit: int = 10) -> str:
    """List recently generated images.

    Args:
        limit: Maximum number of images to list

    Returns:
        List of image paths with timestamps
    """
    try:
        images = sorted(OUTPUT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not images:
            return "No generated images found."

        result = f"Recent images (showing {min(len(images), limit)} of {len(images)}):\n"
        for img in images[:limit]:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(img.stat().st_mtime))
            result += f"  {mtime} - {img.name}\n"

        return result

    except Exception as e:
        return f"Error listing images: {type(e).__name__}: {e}"


# Export tools
IMAGE_GEN_TOOLS = [generate_image, list_generated_images]
