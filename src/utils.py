#!/usr/bin/env python3
"""Shared utilities for TaskNotes Alfred workflow.

Provides common functionality used across multiple modules:
- Emoji icon generation for Alfred items
- HTTP request helpers
- Task status checking
- Constants
"""

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from cache import get_cache_dir


# -----------------------------
# Constants
# -----------------------------
PRIORITY_HIGH = "High"
PRIORITY_MEDIUM = "Medium"
PRIORITY_LOW = "Low"

PRIORITY_MAP = {
    "p1": PRIORITY_HIGH,
    "p2": PRIORITY_MEDIUM,
    "p3": PRIORITY_LOW,
}

# Status values that indicate completion
COMPLETED_STATUSES = {"done", "completed", "complete"}
ARCHIVED_STATUSES = {"archived"}


# -----------------------------
# Configuration
# -----------------------------
def get_tasknotes_api_base() -> str:
    """Get the TaskNotes API base URL."""
    return os.environ.get("TASKNOTES_API_BASE", "http://localhost:8080/api").rstrip("/")


def get_tasknotes_token() -> str:
    """Get the TaskNotes authentication token."""
    return (os.environ.get("TASKNOTES_TOKEN") or "").strip()


# -----------------------------
# Emoji icon generation
# -----------------------------
def get_emoji_icon_path(emoji: str, name: str) -> str:
    """
    Get path to a PNG icon for the given emoji, generating it if needed.

    Uses Pillow to render emoji to PNG and caches in Alfred's cache dir.
    Falls back to empty string (default icon) if generation fails.

    Args:
        emoji: The emoji character to render
        name: A unique name for caching (e.g., "filter_today", "action_complete")

    Returns:
        Path to the generated PNG icon, or empty string on failure
    """
    cache_dir = Path(get_cache_dir()) / "icons"
    cache_dir.mkdir(parents=True, exist_ok=True)

    icon_path = cache_dir / f"{name}.png"

    # Return cached icon if it exists
    if icon_path.exists():
        return str(icon_path)

    try:
        from PIL import Image, ImageDraw, ImageFont

        # Create image with transparent background
        size = 64
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Try to use Apple Color Emoji font, fall back to default
        font = None
        font_paths = [
            "/System/Library/Fonts/Apple Color Emoji.ttc",
            "/System/Library/Fonts/AppleColorEmoji.ttf",
        ]
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, 48)
                    break
                except Exception:
                    continue

        if font is None:
            # Use default font as fallback
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
            except Exception:
                font = ImageFont.load_default()

        # Draw emoji centered
        bbox = draw.textbbox((0, 0), emoji, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (size - text_width) // 2 - bbox[0]
        y = (size - text_height) // 2 - bbox[1]
        draw.text((x, y), emoji, font=font, embedded_color=True)

        img.save(str(icon_path), 'PNG')

    except Exception:
        # Fall back to default icon if generation fails
        return ""

    return str(icon_path) if icon_path.exists() else ""


# -----------------------------
# HTTP helpers
# -----------------------------
def http_get_json(url: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """
    Fetch JSON from URL with TaskNotes authentication.

    Args:
        url: Full URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON dict, or None on any error
    """
    headers = {"Accept": "application/json"}
    token = get_tasknotes_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except Exception:
        return None


# -----------------------------
# Task field accessors
# -----------------------------
def get_field(task: Any, key: str, default: Any = None) -> Any:
    """Get field from task (dict or object)."""
    if isinstance(task, dict):
        return task.get(key, default)
    return getattr(task, key, default)


def is_completed(task: Any) -> bool:
    """Check if a task is completed."""
    v = get_field(task, "completed", None)
    if v is not None:
        return bool(v)
    status = str(get_field(task, "status", "") or "").lower()
    return status in COMPLETED_STATUSES


def is_archived(task: Any) -> bool:
    """Check if a task is archived."""
    v = get_field(task, "archived", None)
    if v is not None:
        return bool(v)
    status = str(get_field(task, "status", "") or "").lower()
    return status in ARCHIVED_STATUSES


# -----------------------------
# Workflow icon helper
# -----------------------------
def get_workflow_icon_path() -> str:
    """Get path to the workflow's icon.png."""
    alfred_preferences = os.environ.get("alfred_preferences", "")
    alfred_workflow_uid = os.environ.get("alfred_workflow_uid", "")
    if alfred_preferences and alfred_workflow_uid:
        icon_path = os.path.join(alfred_preferences, "workflows", alfred_workflow_uid, "icon.png")
        if os.path.exists(icon_path):
            return icon_path
    return ""
