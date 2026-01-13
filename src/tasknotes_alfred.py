#!/usr/bin/env python3
"""tasknotes_alfred.py

Shared TaskNotes <-> Alfred helpers.

Key design goals:
  - Prefer the TaskNotes HTTP API server-side filtering/sorting where available.
  - Fall back gracefully to client-side filtering if the API rejects a query.

TaskNotes API reference:
  - GET /api/tasks supports query params such as completed, archived, sort, limit, etc.
    See https://tasknotes.dev/HTTP_API/.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Config (Environment Variables)
# -----------------------------

TASKNOTES_API_BASE = os.environ.get("TASKNOTES_API_BASE", "http://localhost:8080/api").rstrip("/")
TASKNOTES_TOKEN = os.environ.get("TASKNOTES_TOKEN", "").strip()


# -----------------------------
# HTTP helpers
# -----------------------------

class APIError(RuntimeError):
    pass


def _request_json(method: str, path: str, body: Optional[dict] = None) -> dict:
    url = f"{TASKNOTES_API_BASE}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if TASKNOTES_TOKEN:
        headers["Authorization"] = f"Bearer {TASKNOTES_TOKEN}"

    data: Optional[bytes] = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                raise APIError(f"Non-JSON response from TaskNotes API: {raw[:2000]}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        # Try to parse JSON error
        try:
            payload = json.loads(raw)
            msg = payload.get("error") or payload.get("message") or raw
        except Exception:
            msg = raw or str(e)
        raise APIError(f"HTTP {e.code} calling {url}: {msg}")
    except urllib.error.URLError as e:
        raise APIError(f"Failed to connect to TaskNotes API at {TASKNOTES_API_BASE}: {e}")

    # TaskNotes responses are typically shaped like:
    #   { "success": true|false, "data": ..., "error": ... }
    if isinstance(payload, dict) and payload.get("success") is False:
        raise APIError(payload.get("error") or "Unknown TaskNotes API error")

    return payload


# -----------------------------
# Task Model + Normalization
# -----------------------------

_COMPLETED_STATUS = {"done", "completed", "complete"}
_ARCHIVED_STATUS = {"archived"}


@dataclass
class Task:
    id: str
    path: str
    title: str
    status: str
    priority: str
    due: str
    scheduled: str
    tags: List[str]
    projects: List[str]
    contexts: List[str]
    date_created: str
    date_modified: str
    details: str
    completed: bool = False
    archived: bool = False


def normalize_task(raw: dict) -> Task:
    if not isinstance(raw, dict):
        raise APIError("Invalid task payload from TaskNotes")

    path = str(raw.get("path", "") or "")
    title = str(raw.get("title", "") or "")
    status = str(raw.get("status", "") or "")
    priority = str(raw.get("priority", "") or "")
    due = str(raw.get("due", "") or "")
    scheduled = str(raw.get("scheduled", "") or "")
    details = str(raw.get("details", "") or "")

    tags = raw.get("tags") or []
    projects = raw.get("projects") or []
    contexts = raw.get("contexts") or []

    if not isinstance(tags, list): tags = []
    if not isinstance(projects, list): projects = []
    if not isinstance(contexts, list): contexts = []

    date_created = str(raw.get("date_created", "") or "")
    date_modified = str(raw.get("date_modified", "") or "")

    # Prefer explicit fields if present, otherwise infer from status string.
    completed = bool(raw.get("completed")) if "completed" in raw else (status.lower() in _COMPLETED_STATUS)
    archived = bool(raw.get("archived")) if "archived" in raw else (status.lower() in _ARCHIVED_STATUS)

    # TaskNotes uses the task file path as the task identifier.
    task_id = path
    return Task(
        id=task_id,
        path=path,
        title=title,
        status=status,
        priority=priority,
        due=due,
        scheduled=scheduled,
        tags=[str(t) for t in tags if t is not None],
        projects=[str(p) for p in projects if p is not None],
        contexts=[str(c) for c in contexts if c is not None],
        date_created=date_created,
        date_modified=date_modified,
        details=details,
        completed=completed,
        archived=archived,
    )


def task_to_dict(t: Task) -> Dict[str, Any]:
    """Convert a normalized Task into a JSON-serialisable dict.

    We use TaskNotes' field names so downstream scripts can
    treat cached tasks the same as raw API payloads.
    """
    return {
        "path": t.path,
        "title": t.title,
        "status": t.status,
        "priority": t.priority,
        "due": t.due,
        "scheduled": t.scheduled,
        "tags": list(t.tags),
        "projects": list(t.projects),
        "contexts": list(t.contexts),
        "date_created": t.date_created,
        "date_modified": t.date_modified,
        "details": t.details,
        "completed": bool(t.completed),
        "archived": bool(t.archived),
    }


# -----------------------------
# Task listing
# -----------------------------

def _build_query(params: Dict[str, Any]) -> str:
    """Build a query string for GET /tasks.

    TaskNotes uses simple scalar query params; `urlencode` handles correct
    percent-encoding.
    """
    # Remove None/empty values so we don't send meaningless params.
    cleaned: Dict[str, Any] = {k: v for k, v in params.items() if v is not None and v != ""}
    return urllib.parse.urlencode(cleaned, doseq=True)


def list_tasks(
    *,
    limit: int = 200,
    offset: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    project: Optional[str] = None,
    tag: Optional[str] = None,
    overdue: Optional[bool] = None,
    completed: Optional[bool] = None,
    archived: Optional[bool] = None,
    due_before: Optional[str] = None,
    due_after: Optional[str] = None,
    sort: Optional[str] = None,
) -> List[Task]:
    """List tasks using TaskNotes' server-side filtering when supported.

    TaskNotes docs: GET /api/tasks (limit, completed, archived, sort, ...)
    https://tasknotes.dev/HTTP_API/.

    If the API rejects a parameter (older versions), we fall back to a plain
    GET /tasks?limit=... and filter client-side (best-effort).
    """
    params: Dict[str, Any] = {
        "limit": int(limit),
        "offset": int(offset) if offset is not None else None,
        "status": status,
        "priority": priority,
        "project": project,
        "tag": tag,
        "overdue": str(overdue).lower() if overdue is not None else None,
        "completed": str(completed).lower() if completed is not None else None,
        "archived": str(archived).lower() if archived is not None else None,
        "due_before": due_before,
        "due_after": due_after,
        "sort": sort,
    }

    # Prefer server-side filters.
    path = "/tasks"
    q = _build_query(params)
    if q:
        path = f"{path}?{q}"

    try:
        payload = _request_json("GET", path)
    except APIError:
        # Fall back to the minimum supported call shape.
        payload = _request_json("GET", f"/tasks?limit={int(limit)}")

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    tasks = data.get("tasks", []) if isinstance(data, dict) else []

    out: List[Task] = []
    if isinstance(tasks, list):
        for t in tasks:
            try:
                out.append(normalize_task(t))
            except Exception:
                continue
    return out


# -----------------------------
# Task actions
# -----------------------------

def create_task(
    title: str,
    *,
    due: Optional[str] = None,
    scheduled: Optional[str] = None,
    priority: Optional[str] = None,
    tags: Optional[List[str]] = None,
    projects: Optional[List[str]] = None,
    details: Optional[str] = None,
    status: Optional[str] = None,
    time_estimate: Optional[int] = None,
) -> Task:
    """Create a new task.

    TaskNotes HTTP API supports (among others):
      title, priority, status, due, scheduled, tags, projects, contexts, details.
    We keep this helper permissive and only send fields that are provided.

    Docs: https://tasknotes.dev/HTTP_API/
    """
    title = (title or "").strip()
    if not title:
        raise APIError("Cannot create a blank task title")

    body: Dict[str, Any] = {"title": title}
    if due:
        body["due"] = str(due)
    if scheduled:
        body["scheduled"] = str(scheduled)
    if priority:
        body["priority"] = str(priority)
    if status:
        body["status"] = str(status)
    if details:
        body["details"] = str(details)
    if time_estimate is not None:
        body["time_estimate"] = int(time_estimate)

    if tags:
        body["tags"] = [str(t) for t in tags if str(t).strip()]
    if projects:
        body["projects"] = [str(p) for p in projects if str(p).strip()]

    payload = _request_json("POST", "/tasks", body=body)
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    # Some versions return created task under data.task, others return task directly.
    raw = data.get("task") if isinstance(data, dict) and "task" in data else data
    return normalize_task(raw)


# -----------------------------
# Pomodoro API
# -----------------------------

@dataclass
class PomodoroStatus:
    has_session: bool  # True if currentSession exists (active or paused)
    is_running: bool   # True if timer is actively counting
    is_paused: bool
    time_remaining: int  # seconds
    session_type: str  # "work" or "break"
    task_id: Optional[str]
    task_title: Optional[str]
    total_pomodoros: int
    current_streak: int


def get_pomodoro_status() -> Optional[PomodoroStatus]:
    """Get current pomodoro status from TaskNotes API."""
    try:
        payload = _request_json("GET", "/pomodoro/status")
    except APIError:
        return None

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return None

    is_running = bool(data.get("isRunning", False))
    time_remaining = int(data.get("timeRemaining", 0) or 0)
    total_pomodoros = int(data.get("totalPomodoros", 0) or 0)
    current_streak = int(data.get("currentStreak", 0) or 0)

    session = data.get("currentSession") or {}
    has_session = bool(session)  # True if session exists (even if paused)
    # Infer paused state: session exists but timer not running
    is_paused = has_session and not is_running
    session_type = str(session.get("type", "work") or "work")
    task_id = session.get("taskId") or None
    task_title = session.get("taskTitle") or None

    return PomodoroStatus(
        has_session=has_session,
        is_running=is_running,
        is_paused=is_paused,
        time_remaining=time_remaining,
        session_type=session_type,
        task_id=task_id,
        task_title=task_title,
        total_pomodoros=total_pomodoros,
        current_streak=current_streak,
    )


def start_pomodoro(task_id: Optional[str] = None) -> Dict[str, Any]:
    """Start a pomodoro session, optionally with a task."""
    body: Optional[Dict[str, Any]] = None
    if task_id:
        body = {"taskId": task_id}
    return _request_json("POST", "/pomodoro/start", body=body)


def stop_pomodoro() -> Dict[str, Any]:
    """Stop the current pomodoro session."""
    return _request_json("POST", "/pomodoro/stop")


def pause_pomodoro() -> Dict[str, Any]:
    """Pause the current pomodoro session."""
    return _request_json("POST", "/pomodoro/pause")


def resume_pomodoro() -> Dict[str, Any]:
    """Resume a paused pomodoro session."""
    return _request_json("POST", "/pomodoro/resume")


# -----------------------------
# Alfred Output helper (optional)
# -----------------------------

def alfred_error_item(title: str, subtitle: str) -> Dict[str, Any]:
    return {
        "title": title,
        "subtitle": subtitle,
        "valid": False,
    }


if __name__ == "__main__":
    # Tiny manual test: `python3 tasknotes_alfred.py`
    tasks = list_tasks(limit=50)
    for t in tasks[:10]:
        print(f"- {t.title} ({t.path})")
