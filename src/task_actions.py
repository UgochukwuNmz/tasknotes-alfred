#!/usr/bin/env python3
"""Task Actions menu for Alfred.

Displays a list of actions for a selected task.
Receives task path as input, outputs Alfred JSON with action items.
"""

import json
import os
import sys
import urllib.parse
from typing import Any, Dict, List, Optional

from utils import (
    get_emoji_icon_path,
    get_tasknotes_api_base,
    get_workflow_icon_path,
    http_get_json,
    is_archived as _is_task_archived,
    is_completed as _is_task_completed,
)


# -----------------------------
# API helpers
# -----------------------------
def _get_task_details(task_path: str) -> Optional[Dict[str, Any]]:
    """Fetch task details from API."""
    enc_path = urllib.parse.quote(task_path, safe="")
    base = get_tasknotes_api_base()
    payload = http_get_json(f"{base}/tasks/{enc_path}", timeout=2.0)

    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict):
            return payload.get("data")
        elif "title" in payload and "path" in payload:
            return payload
    return None


def _get_active_tracking_task_id() -> str:
    """Get the ID of the currently tracked task, if any."""
    base = get_tasknotes_api_base()
    payload = http_get_json(f"{base}/time/active", timeout=2.0)

    try:
        data = (payload or {}).get("data") or {}
        sessions = data.get("activeSessions") or []
        if isinstance(sessions, list) and sessions:
            first = sessions[0] if isinstance(sessions[0], dict) else {}
            task = first.get("task") or {}
            return str(task.get("id") or "")
    except Exception:
        pass
    return ""


# -----------------------------
# Alfred output
# -----------------------------
def _alfred_output(items: List[Dict[str, Any]]) -> None:
    """Write Script Filter JSON to stdout."""
    print(json.dumps({"items": items}, ensure_ascii=False))


def _build_action_item(
    title: str,
    subtitle: str,
    arg: Dict[str, Any],
    icon_emoji: str,
    icon_name: str,
    *,
    valid: bool = True,
    autocomplete: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an Alfred item for an action."""
    item: Dict[str, Any] = {
        "title": title,
        "subtitle": subtitle,
        "arg": json.dumps(arg, ensure_ascii=False),
        "valid": valid,
    }

    if autocomplete is not None:
        item["autocomplete"] = autocomplete

    icon_path = get_emoji_icon_path(icon_emoji, icon_name)
    if icon_path:
        item["icon"] = {"path": icon_path}

    return item


# -----------------------------
# Helpers
# -----------------------------
def _title_from_path(path: str) -> str:
    """Extract a display title from a task path (fallback when API unavailable)."""
    # Path is like "Tasks/Some Task Name.md" or "Some Task Name.md"
    # Extract just the filename without extension
    import os.path
    filename = os.path.basename(path)
    if filename.lower().endswith(".md"):
        filename = filename[:-3]
    return filename.strip() or "Untitled Task"


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    """Main entry point."""
    # Get task path from input
    task_path = (sys.argv[1] if len(sys.argv) > 1 else "").strip()

    if not task_path:
        _alfred_output([{
            "title": "No task selected",
            "subtitle": "Go back and select a task",
            "valid": False,
        }])
        return 0

    # Fetch task details
    task = _get_task_details(task_path)

    # Use API title if available, otherwise extract from path
    if task and task.get("title"):
        task_title = task.get("title")
    else:
        task_title = _title_from_path(task_path)

    is_completed = _is_task_completed(task) if task else False
    is_archived = _is_task_archived(task) if task else False

    # Check tracking state
    active_tracking_id = _get_active_tracking_task_id()
    is_tracking = active_tracking_id == task_path

    # Build action items
    items: List[Dict[str, Any]] = []

    # Header showing task title
    workflow_icon = get_workflow_icon_path()
    header_item: Dict[str, Any] = {
        "title": task_title,
        "subtitle": "Select an action below",
        "valid": False,
    }
    if workflow_icon:
        header_item["icon"] = {"path": workflow_icon}
    items.append(header_item)

    # Open Task
    items.append(_build_action_item(
        "Open Task",
        "Open in Obsidian",
        {"action": "open", "path": task_path},
        "📂", "action_open",
    ))

    # Complete/Reopen Task
    if is_completed:
        items.append(_build_action_item(
            "Reopen Task",
            "Mark as incomplete",
            {"action": "toggle_complete", "path": task_path},
            "🔄", "action_reopen",
        ))
    else:
        items.append(_build_action_item(
            "Complete Task",
            "Mark as done",
            {"action": "toggle_complete", "path": task_path},
            "✅", "action_complete",
        ))

    # Archive/Unarchive Task
    if is_archived:
        items.append(_build_action_item(
            "Unarchive Task",
            "Restore from archive",
            {"action": "toggle_archive", "path": task_path},
            "📤", "action_unarchive",
        ))
    else:
        items.append(_build_action_item(
            "Archive Task",
            "Move to archive",
            {"action": "toggle_archive", "path": task_path},
            "📦", "action_archive",
        ))

    # Time Tracking
    if is_tracking:
        items.append(_build_action_item(
            "Stop Time Tracking",
            "Stop the current tracking session",
            {"action": "toggle_tracking", "path": task_path},
            "⏹️", "action_stop_tracking",
        ))
    else:
        items.append(_build_action_item(
            "Start Time Tracking",
            "Begin tracking time on this task",
            {"action": "toggle_tracking", "path": task_path},
            "⏱️", "action_start_tracking",
        ))

    # Schedule for Today
    items.append(_build_action_item(
        "Schedule for Today",
        "Set scheduled date to today",
        {"action": "schedule_today", "path": task_path},
        "📅", "action_schedule_today",
    ))

    # Delete Task
    items.append(_build_action_item(
        "Delete Task",
        "Move to trash",
        {"action": "delete", "path": task_path, "title": task_title},
        "🗑️", "action_delete",
    ))

    # Go Back
    items.append(_build_action_item(
        "Go Back",
        "Return to task list",
        {"action": "go_back"},
        "⬅️", "action_back",
    ))

    _alfred_output(items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
