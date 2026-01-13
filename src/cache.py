#!/usr/bin/env python3
"""Cache management for TaskNotes Alfred workflow.

Provides a unified caching layer with:
- TTL-based invalidation
- Stale-while-revalidate pattern
- Atomic writes (temp file + rename)
- Separate caches for tasks, refresh state, active sessions, and task details
"""

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Cache directory
# -----------------------------
def get_cache_dir() -> str:
    """Return the workflow's cache directory, creating it if needed."""
    d = (
        os.environ.get("alfred_workflow_cache")
        or os.environ.get("ALFRED_WORKFLOW_CACHE")
        or os.path.join(tempfile.gettempdir(), "tasknotes-alfred-cache")
    )
    os.makedirs(d, exist_ok=True)
    return d


# -----------------------------
# Low-level JSON file operations
# -----------------------------
def read_json_file(path: str) -> Optional[Dict[str, Any]]:
    """Read and parse a JSON file. Returns None on any error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_json_file(path: str, data: Dict[str, Any]) -> None:
    """Atomically write data to a JSON file using temp file + rename."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# -----------------------------
# Cache file paths
# -----------------------------
def get_task_cache_paths() -> Tuple[str, str]:
    """Return (tasks_cache_path, refresh_state_path)."""
    d = get_cache_dir()
    return (
        os.path.join(d, "tasks_cache.json"),
        os.path.join(d, "tasks_refresh_state.json"),
    )


def get_time_cache_path() -> str:
    """Return path to the active time session cache."""
    return os.path.join(get_cache_dir(), "time_active_cache.json")


def get_task_detail_cache_path() -> str:
    """Return path to the task detail cache."""
    return os.path.join(get_cache_dir(), "task_detail_cache.json")


# -----------------------------
# Task list cache
# -----------------------------
class TaskCache:
    """Manages the main task list cache with TTL and stale-while-revalidate."""

    def __init__(
        self,
        ttl_seconds: int = 5,
        max_stale_seconds: int = 600,
        refresh_backoff_seconds: int = 5,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_stale_seconds = max_stale_seconds
        self.refresh_backoff_seconds = refresh_backoff_seconds
        self._cache_path, self._state_path = get_task_cache_paths()

    def load_cache(self) -> Optional[Dict[str, Any]]:
        """Load the task cache from disk."""
        return read_json_file(self._cache_path)

    def save_cache(self, tasks: List[Dict[str, Any]]) -> None:
        """Save tasks to cache with current timestamp."""
        write_json_file(
            self._cache_path,
            {
                "version": 1,
                "timestamp": time.time(),
                "tasks": tasks,
            },
        )

    def load_refresh_state(self) -> Dict[str, Any]:
        """Load the refresh state (tracks ongoing refreshes)."""
        return read_json_file(self._state_path) or {}

    def save_refresh_state(self, state: Dict[str, Any]) -> None:
        """Save refresh state to disk."""
        write_json_file(self._state_path, state)

    def get_cache_status(self) -> Dict[str, Any]:
        """
        Analyze current cache state and return status info.

        Returns dict with:
        - tasks: List of cached tasks (or empty list)
        - age: Cache age in seconds (or None if no cache)
        - is_fresh: Whether cache is within TTL
        - is_usable: Whether cache is within max stale window
        - refresh_requested: Whether a background refresh was requested
        - should_fetch: Whether a fresh fetch should be attempted
        """
        now = time.time()
        cache = self.load_cache() or {}
        state = self.load_refresh_state()

        tasks: List[Dict[str, Any]] = []
        age: Optional[float] = None

        if isinstance(cache.get("tasks"), list) and isinstance(cache.get("timestamp"), (int, float)):
            tasks = [t for t in cache.get("tasks", []) if isinstance(t, dict)]
            age = now - float(cache["timestamp"])

        is_usable = bool(tasks) and (age is not None) and (age <= self.max_stale_seconds)
        is_fresh = is_usable and (age is not None) and (age <= self.ttl_seconds)
        refresh_requested = bool(state.get("refresh_requested"))
        last_attempt = float(state.get("last_attempt", 0) or 0)

        # Determine if we should fetch
        should_fetch = True
        if refresh_requested and (now - last_attempt) < self.refresh_backoff_seconds and is_usable:
            should_fetch = False

        return {
            "tasks": tasks,
            "age": age,
            "is_fresh": is_fresh,
            "is_usable": is_usable,
            "refresh_requested": refresh_requested,
            "should_fetch": should_fetch,
            "state": state,
        }

    def mark_refresh_requested(self) -> None:
        """Mark that a background refresh has been requested."""
        state = self.load_refresh_state()
        state["refresh_requested"] = True
        state["requested_at"] = time.time()
        self.save_refresh_state(state)

    def mark_fetch_attempt(self) -> None:
        """Mark that a fetch attempt is starting."""
        state = self.load_refresh_state()
        state["last_attempt"] = time.time()
        self.save_refresh_state(state)

    def mark_fetch_success(self, tasks: List[Dict[str, Any]]) -> None:
        """Mark a successful fetch and save the new cache."""
        self.save_cache(tasks)
        state = self.load_refresh_state()
        state["refresh_requested"] = False
        state["last_success"] = time.time()
        self.save_refresh_state(state)


# -----------------------------
# Time tracking session cache
# -----------------------------
class TimeSessionCache:
    """Manages the active time tracking session cache."""

    def __init__(self, ttl_seconds: int = 1):
        self.ttl_seconds = ttl_seconds
        self._cache_path = get_time_cache_path()

    def get_cached_session(self) -> Optional[Dict[str, Any]]:
        """
        Return cached active session if still valid, else None.

        Cached session shape:
        {
            "id": "path/to/task.md",
            "title": "Task Title",
            "elapsedMinutes": 25,
            "tags": [...],
            "projects": [...],
            "priority": "normal",
            "status": "none"
        }
        """
        now = time.time()
        cached = read_json_file(self._cache_path) or {}
        ts = cached.get("timestamp")

        if isinstance(ts, (int, float)) and (now - float(ts)) <= max(0, self.ttl_seconds):
            session = cached.get("active")
            return session if isinstance(session, dict) else None

        return None

    def save_session(self, active_session: Optional[Dict[str, Any]]) -> None:
        """Save the active session to cache."""
        write_json_file(
            self._cache_path,
            {"timestamp": time.time(), "active": active_session},
        )


# -----------------------------
# Task detail cache (single task)
# -----------------------------
class TaskDetailCache:
    """Manages single-task detail cache for tracked task injection."""

    def __init__(self, ttl_seconds: int = 2):
        self.ttl_seconds = ttl_seconds
        self._cache_path = get_task_detail_cache_path()

    def get_cached_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Return cached task detail if ID matches and still valid."""
        if not task_id:
            return None

        now = time.time()
        cached = read_json_file(self._cache_path) or {}

        cached_id = cached.get("id")
        ts = cached.get("timestamp")

        if (
            isinstance(cached_id, str)
            and cached_id == task_id
            and isinstance(ts, (int, float))
            and (now - float(ts)) <= max(0, self.ttl_seconds)
        ):
            task = cached.get("task")
            return task if isinstance(task, dict) else None

        return None

    def save_task(self, task_id: str, task: Dict[str, Any]) -> None:
        """Save task detail to cache."""
        write_json_file(
            self._cache_path,
            {"timestamp": time.time(), "id": task_id, "task": task},
        )


# -----------------------------
# Pomodoro status cache
# -----------------------------
def get_pomodoro_cache_path() -> str:
    """Return path to the pomodoro status cache."""
    return os.path.join(get_cache_dir(), "pomodoro_status_cache.json")


class PomodoroCache:
    """Manages the pomodoro status cache."""

    def __init__(self, ttl_seconds: int = 1, max_stale_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self.max_stale_seconds = max_stale_seconds
        self._cache_path = get_pomodoro_cache_path()

    def get_cached_status(self) -> Optional[Dict[str, Any]]:
        """
        Return cached pomodoro status if still valid, else None.

        Cached status shape:
        {
            "is_running": bool,
            "is_paused": bool,
            "time_remaining": int,  # seconds
            "session_type": str,    # "work" or "break"
            "task_id": Optional[str],
            "task_title": Optional[str],
            "total_pomodoros": int,
            "current_streak": int,
        }
        """
        now = time.time()
        cached = read_json_file(self._cache_path) or {}
        ts = cached.get("timestamp")

        if isinstance(ts, (int, float)) and (now - float(ts)) <= max(0, self.ttl_seconds):
            status = cached.get("status")
            return status if isinstance(status, dict) else None

        return None

    def get_stale_status(self) -> Optional[Dict[str, Any]]:
        """Return cached status even if stale (up to max_stale_seconds)."""
        now = time.time()
        cached = read_json_file(self._cache_path) or {}
        ts = cached.get("timestamp")

        if isinstance(ts, (int, float)) and (now - float(ts)) <= self.max_stale_seconds:
            status = cached.get("status")
            return status if isinstance(status, dict) else None

        return None

    def save_status(self, status: Optional[Dict[str, Any]]) -> None:
        """Save the pomodoro status to cache."""
        write_json_file(
            self._cache_path,
            {"timestamp": time.time(), "status": status},
        )
