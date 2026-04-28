"""Vercel Deployment Tool for Nexus agent — deploy projects to Vercel."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.tools import tool

# Load environment
load_dotenv(Path.home() / "AI_Agent" / ".env")

VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "")


def _check_vercel_installed() -> bool:
    """Check if Vercel CLI is installed."""
    try:
        result = subprocess.run(
            ["vercel", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@tool
def vercel_deploy(
    project_dir: str,
    project_name: Optional[str] = None,
    prod: bool = False,
    approve: bool = False,
) -> str:
    """Deploy a project to Vercel.

    Args:
        project_dir: Path to the project directory to deploy
        project_name: Optional project name for Vercel (uses directory name if not provided)
        prod: If True, deploy to production. Otherwise, creates a preview deployment.
        approve: must be True to actually deploy. Default False returns a dry-run preview.

    Returns:
        Deployment URL or error message
    """
    if not approve:
        target = "production" if prod else "preview"
        return (
            "DRY-RUN: vercel_deploy not executed.\n"
            f"project_dir: {project_dir}\nproject_name: {project_name}\ntarget: {target}\n"
            "to actually deploy, call again with approve=True."
        )
    if not VERCEL_TOKEN:
        return (
            "Error: VERCEL_TOKEN not configured.\n"
            "Add VERCEL_TOKEN=your_token to ~/AI_Agent/.env\n\n"
            "Get a token from: https://vercel.com/account/tokens"
        )

    if not _check_vercel_installed():
        return (
            "Error: Vercel CLI not installed.\n"
            "Install with: sudo npm install -g vercel\n"
            "(Add to /tmp/sudo-commands.sh for batch install)"
        )

    project_path = Path(project_dir)
    if not project_path.exists():
        return f"Error: Project directory not found: {project_dir}"

    if not project_path.is_dir():
        return f"Error: Path is not a directory: {project_dir}"

    # Check for common project indicators
    has_package = (project_path / "package.json").exists()
    has_index = (project_path / "index.html").exists()
    if not has_package and not has_index:
        return f"Warning: No package.json or index.html found in {project_dir}. Deploy may fail."

    try:
        # Build the vercel command
        cmd = ["vercel", "--token", VERCEL_TOKEN, "--yes"]

        if project_name:
            cmd.extend(["--name", project_name])

        if prod:
            cmd.append("--prod")

        # Run vercel deploy
        result = subprocess.run(
            cmd,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            env={**os.environ, "VERCEL_TOKEN": VERCEL_TOKEN},
        )

        if result.returncode != 0:
            return f"Deployment failed:\n{result.stderr or result.stdout}"

        # Extract URL from output (usually last line)
        output_lines = result.stdout.strip().split("\n")
        deploy_url = None
        for line in reversed(output_lines):
            if "vercel.app" in line or "https://" in line:
                deploy_url = line.strip()
                break

        if deploy_url:
            deploy_type = "Production" if prod else "Preview"
            return f"{deploy_type} deployment successful!\nURL: {deploy_url}"
        else:
            return f"Deployment completed.\nOutput: {result.stdout}"

    except subprocess.TimeoutExpired:
        return "Error: Deployment timed out (5 minute limit)"
    except Exception as e:
        return f"Error deploying: {type(e).__name__}: {e}"


@tool
def vercel_list_deployments(limit: int = 10) -> str:
    """List recent Vercel deployments.

    Args:
        limit: Maximum number of deployments to list

    Returns:
        List of recent deployments or error message
    """
    if not VERCEL_TOKEN:
        return "Error: VERCEL_TOKEN not configured. Add it to ~/AI_Agent/.env"

    if not _check_vercel_installed():
        return "Error: Vercel CLI not installed. Run: sudo npm install -g vercel"

    try:
        result = subprocess.run(
            ["vercel", "ls", "--token", VERCEL_TOKEN, "-n", str(limit)],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "VERCEL_TOKEN": VERCEL_TOKEN},
        )

        if result.returncode != 0:
            return f"Error listing deployments:\n{result.stderr}"

        return f"Recent deployments:\n{result.stdout}"

    except subprocess.TimeoutExpired:
        return "Error: Request timed out"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


@tool
def vercel_remove_deployment(url: str) -> str:
    """Remove a Vercel deployment.

    Args:
        url: The deployment URL to remove

    Returns:
        Success or error message
    """
    if not VERCEL_TOKEN:
        return "Error: VERCEL_TOKEN not configured."

    if not _check_vercel_installed():
        return "Error: Vercel CLI not installed."

    try:
        result = subprocess.run(
            ["vercel", "remove", url, "--token", VERCEL_TOKEN, "--yes"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "VERCEL_TOKEN": VERCEL_TOKEN},
        )

        if result.returncode != 0:
            return f"Error removing deployment:\n{result.stderr}"

        return f"Deployment removed: {url}"

    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# Export tools
VERCEL_TOOLS = [vercel_deploy, vercel_list_deployments, vercel_remove_deployment]
