#!/usr/bin/env python3
"""Shared utilities for TaskNotes Alfred workflow.

Provides common functionality used across multiple modules:
- Emoji icon lookup for Alfred items
- HTTP request helpers
- Task status checking
- Constants
"""

import json
import os
import urllib.request
from typing import Any, Dict, Optional


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
# Emoji icon lookup
# -----------------------------
def get_emoji_icon_path(emoji: str, name: str) -> str:
    """
    Get path to a bundled PNG icon for the given name.

    Icons are pre-generated and bundled in the workflow's icons/ directory.

    Args:
        emoji: The emoji character (unused, kept for API compatibility)
        name: Icon name (e.g., "today", "action_complete")

    Returns:
        Path to the bundled PNG icon, or empty string if not found
    """
    # Get workflow directory (parent of src/)
    workflow_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(workflow_dir, "icons", f"{name}.png")

    if os.path.exists(icon_path):
        return icon_path

    return ""


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
