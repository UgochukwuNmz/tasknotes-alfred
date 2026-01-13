#!/usr/bin/env python3
"""Task Actions menu for Alfred.

Displays a list of actions for a selected task.
Receives task path as input, outputs Alfred JSON with action items.
"""

import json
import os
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from cache import TaskCache
from utils import (
    get_emoji_icon_path,
    get_tasknotes_api_base,
    get_workflow_icon_path,
    http_get_json,
    is_archived as _is_task_archived,
    is_completed as _is_task_completed,
)
from tasknotes_alfred import list_tasks as tn_list_tasks


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


def _search_task_by_title(title: str) -> Optional[Dict[str, Any]]:
    """Search for a task by title (including archived tasks).

    Used as fallback when primary path lookup fails (e.g., task was archived/moved).
    Searches both active and archived tasks.
    """
    if not title:
        return None

    try:
        # Search active tasks first, then archived
        for archived in [False, True]:
            tasks = tn_list_tasks(limit=500, archived=archived)

            for task in tasks:
                if task.title == title:
                    return {
                        "path": task.path,
                        "title": task.title,
                        "status": task.status,
                        "priority": task.priority,
                        "scheduled": task.scheduled,
                        "archived": task.archived,
                        "completed": task.completed,
                    }
    except Exception:
        pass

    return None


def _find_task_in_cache(task_path: str) -> Optional[Dict[str, Any]]:
    """Find task in local cache when API is unavailable.

    Used as fallback when API calls fail (e.g., Obsidian is closed).
    The cache was populated by list_or_parse_task.py when user saw the task list.
    """
    if not task_path:
        return None

    cache = TaskCache()
    status = cache.get_cache_status()
    tasks = status.get("tasks", [])

    for task in tasks:
        if task.get("path") == task_path:
            return task
    return None


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
        _alfred_output([
            {
                "title": "No task selected",
                "subtitle": "Go back and select a task",
                "valid": False,
            },
            _build_action_item(
                "Go Back",
                "Return to task list",
                {"action": "go_back"},
                "⬅️", "action_back",
            ),
        ])
        return 0

    # Fetch task details and tracking state in parallel for better performance
    with ThreadPoolExecutor(max_workers=2) as executor:
        task_future = executor.submit(_get_task_details, task_path)
        tracking_future = executor.submit(_get_active_tracking_task_id)

        task = task_future.result()
        active_tracking_id = tracking_future.result()

    # Fallback: if task not found at path, search by title
    # This handles cases like archived tasks that moved to a different folder
    if task is None:
        title_from_path = _title_from_path(task_path)
        found_task = _search_task_by_title(title_from_path)
        if found_task and found_task.get("path"):
            task = found_task
            task_path = str(found_task.get("path"))  # Update to correct path
        else:
            # Try cache before giving up (handles Obsidian closed scenario)
            cached_task = _find_task_in_cache(task_path)
            if cached_task:
                task = cached_task
            else:
                # Task not found - show error with Go Back option
                _alfred_output([
                    {
                        "title": "Task not found",
                        "subtitle": f"Could not find \"{title_from_path}\" - it may have been deleted",
                        "valid": False,
                    },
                    _build_action_item(
                        "Go Back",
                        "Return to task list",
                        {"action": "go_back"},
                        "⬅️", "action_back",
                    ),
                ])
                return 0

    # Use API title if available, otherwise extract from path
    if task and task.get("title"):
        task_title = task.get("title")
    else:
        task_title = _title_from_path(task_path)

    is_completed = _is_task_completed(task) if task else False
    is_archived = _is_task_archived(task) if task else False
    has_scheduled = bool(str(task.get("scheduled", "") or "").strip()) if task else False

    # Check tracking state (active_tracking_id was fetched in parallel above)
    is_tracking = active_tracking_id == task_path

    # Build action items
    items: List[Dict[str, Any]] = []

    # Header showing task title - opens task in Obsidian
    workflow_icon = get_workflow_icon_path()
    header_item: Dict[str, Any] = {
        "title": task_title,
        "subtitle": "Open in Obsidian",
        "arg": json.dumps({"action": "open", "path": task_path}, ensure_ascii=False),
        "valid": True,
    }
    if workflow_icon:
        header_item["icon"] = {"path": workflow_icon}
    items.append(header_item)

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

    # Schedule for Today / Clear Schedule
    if has_scheduled:
        items.append(_build_action_item(
            "Clear Schedule",
            "Remove scheduled date",
            {"action": "toggle_schedule", "path": task_path},
            "🗓️", "action_clear_schedule",
        ))
    else:
        items.append(_build_action_item(
            "Schedule for Today",
            "Set scheduled date to today",
            {"action": "toggle_schedule", "path": task_path},
            "📅", "action_schedule_today",
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
