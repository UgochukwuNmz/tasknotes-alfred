#!/usr/bin/env python3
"""TaskNotes Alfred Script Filter - Search and Create tasks.

Two modes:
- Search mode (default): Fetches tasks, filters/ranks locally, shows results
- Create-only mode (MODE=create_only): Parses input with NLP, shows create item

Refactored for readability with extracted functions and centralized cache management.
"""

import json
import os
import subprocess
import sys
import urllib.parse
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import tasknotes_alfred as tn
from cache import TaskCache, TimeSessionCache, TaskDetailCache
from nlp_task_create import build_preview, parse_create_input
from utils import (
    get_emoji_icon_path,
    get_field,
    get_tasknotes_api_base,
    get_tasknotes_token,
    http_get_json,
    is_archived,
    is_completed,
)


# -----------------------------
# Configuration from environment
# -----------------------------
TASK_FETCH_LIMIT = int(os.environ.get("TASK_FETCH_LIMIT", "400"))
TASK_RETURN_LIMIT = int(os.environ.get("TASK_RETURN_LIMIT", "50"))
TASK_SUBTITLE_FIELDS = os.environ.get("TASK_SUBTITLE_FIELDS", "due,scheduled,projects").strip()
LAUNCH_OBSIDIAN_ON_ERROR = os.environ.get("LAUNCH_OBSIDIAN_ON_ERROR", "1").strip() == "1"

# Cache tuning
TASK_CACHE_TTL_SECONDS = int(os.environ.get("TASK_CACHE_TTL_SECONDS", "5"))
TASK_CACHE_MAX_STALE_SECONDS = int(os.environ.get("TASK_CACHE_MAX_STALE_SECONDS", "600"))
TASK_CACHE_RERUN_SECONDS = float(os.environ.get("TASK_CACHE_RERUN_SECONDS", "0.4"))
TASK_CACHE_REFRESH_BACKOFF_SECONDS = int(os.environ.get("TASK_CACHE_REFRESH_BACKOFF_SECONDS", "5"))

# Time tracking cache
TIME_ACTIVE_CACHE_TTL_SECONDS = int(os.environ.get("TIME_ACTIVE_CACHE_TTL_SECONDS", "1"))
TASK_DETAIL_CACHE_TTL_SECONDS = int(os.environ.get("TASK_DETAIL_CACHE_TTL_SECONDS", "2"))

# Weekday names for relative date display
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# -----------------------------
# Alfred output helpers
# -----------------------------
def _alfred_item(title: str, subtitle: str, *, valid: bool, arg: str = "") -> Dict[str, Any]:
    """Build a single Alfred item dict."""
    it: Dict[str, Any] = {"title": title, "subtitle": subtitle, "valid": bool(valid)}
    if arg:
        it["arg"] = arg
    return it


def _alfred_output(items: List[Dict[str, Any]], *, rerun: Optional[float] = None) -> None:
    """Write Script Filter JSON to stdout."""
    payload: Dict[str, Any] = {"items": items}
    if rerun is not None:
        payload["rerun"] = max(0.1, min(5.0, float(rerun)))
    print(json.dumps(payload, ensure_ascii=False))


# -----------------------------
# Text normalization and tokenization
# -----------------------------
def _norm_title(s: str) -> str:
    """Normalize text for comparison (lowercase, collapse whitespace)."""
    return " ".join((s or "").casefold().split())


def _tokenize(q: str) -> List[str]:
    """Split query into normalized tokens."""
    return [t for t in _norm_title(q).split() if t]


def _csv_fields(s: str) -> List[str]:
    """Parse comma-separated field list."""
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def _format_relative_date(date_str: str) -> str:
    """Format a date string as a relative date (Today, Tomorrow, Mon, etc.)."""
    if not date_str:
        return ""
    try:
        # Parse ISO date (YYYY-MM-DD)
        parts = date_str.split("-")
        if len(parts) != 3:
            return date_str
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        task_date = date(year, month, day)
    except (ValueError, IndexError):
        return date_str

    today = date.today()
    delta = (task_date - today).days

    if delta == 0:
        return "Today"
    elif delta == 1:
        return "Tomorrow"
    elif delta == -1:
        return "Yesterday"
    elif delta < -1:
        return f"{-delta}d ago"
    elif 2 <= delta <= 6:
        return _WEEKDAYS[task_date.weekday()]
    elif delta == 7:
        return "Next week"
    else:
        # Show month and day for dates further out
        return task_date.strftime("%b %d")


def _parse_quick_filter(query: str) -> Tuple[Optional[str], str]:
    """
    Parse quick filter prefix from query.

    Returns: (filter_type, remaining_query)
    filter_type is one of: 'today', 'tomorrow', 'overdue', 'complete', 'archived', 'p1', 'p2', 'p3', or None
    """
    q = query.strip()
    filters = {
        "!today": "today",
        "!tomorrow": "tomorrow",
        "!overdue": "overdue",
        "!complete": "complete",
        "!archived": "archived",
        "!p1": "p1",
        "!p2": "p2",
        "!p3": "p3",
    }

    for prefix, filter_type in filters.items():
        if q.lower() == prefix:
            return (filter_type, "")
        if q.lower().startswith(prefix + " "):
            return (filter_type, q[len(prefix):].strip())

    return (None, query)


# Filter metadata for autocomplete suggestions
# (prefix, filter_type, display_name, description, icon)
_FILTER_INFO = [
    ("!today", "today", "Today", "Tasks due or scheduled for today", "📅"),
    ("!tomorrow", "tomorrow", "Tomorrow", "Tasks due or scheduled for tomorrow", "🗓️"),
    ("!overdue", "overdue", "Overdue", "Tasks past their due or scheduled date", "⚠️"),
    ("!complete", "complete", "Complete", "Completed tasks", "✅"),
    ("!archived", "archived", "Archived", "Archived tasks", "📦"),
    ("!p1", "p1", "P1", "High priority tasks", "🔴"),
    ("!p2", "p2", "P2", "Medium priority tasks", "🟡"),
    ("!p3", "p3", "P3", "Low priority tasks", "🟢"),
]


def _get_matching_filters(partial: str) -> List[Tuple[str, str, str, str, str]]:
    """
    Get filters matching a partial input.

    Args:
        partial: User input starting with '!' (e.g., '!', '!to', '!ov')

    Returns:
        List of matching (prefix, filter_type, display_name, description, icon) tuples
    """
    partial_lower = partial.lower()

    # If just '!', return all filters
    if partial_lower == "!":
        return list(_FILTER_INFO)

    # Match filters that start with the partial input
    matches = [f for f in _FILTER_INFO if f[0].startswith(partial_lower)]
    return matches


def _build_filter_suggestion_items(partial: str) -> List[Dict[str, Any]]:
    """
    Build Alfred items for filter autocomplete suggestions.

    Args:
        partial: User input starting with '!' (e.g., '!', '!to')

    Returns:
        List of Alfred items with autocomplete property
    """
    matches = _get_matching_filters(partial)

    if not matches:
        return []

    items: List[Dict[str, Any]] = []
    for prefix, filter_type, display_name, description, icon in matches:
        item: Dict[str, Any] = {
            "title": display_name,
            "subtitle": description,
            "autocomplete": prefix + " ",  # Trailing space for continued typing
            "valid": False,  # Tab to autocomplete, not Enter
        }
        # Match field for Alfred's built-in filtering
        item["match"] = f"{prefix} {filter_type} {display_name} {description}"

        # Generate and set custom icon
        icon_path = get_emoji_icon_path(icon, filter_type)
        if icon_path:
            item["icon"] = {"path": icon_path}

        items.append(item)

    return items


def _is_partial_filter(query: str) -> bool:
    """
    Check if query is a partial filter that should show suggestions.

    Returns True if:
    - Query is exactly '!'
    - Query starts with '!' but doesn't fully match any filter
    """
    q = query.strip().lower()

    if not q.startswith("!"):
        return False

    # Check if it's an exact match or has content after the filter
    for prefix, _, _, _, _ in _FILTER_INFO:
        if q == prefix or q.startswith(prefix + " "):
            return False  # It's a complete filter, not partial

    # It starts with ! but isn't a complete filter
    return True


def _apply_quick_filter(tasks: List[Dict[str, Any]], filter_type: str) -> List[Dict[str, Any]]:
    """Apply a quick filter to the task list."""
    today_str = date.today().isoformat()
    tomorrow_str = (date.today().replace(day=date.today().day + 1) if date.today().day < 28
                    else date.today()).isoformat()

    # Safer tomorrow calculation
    from datetime import timedelta
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

    filtered: List[Dict[str, Any]] = []

    for t in tasks:
        due = str(get_field(t, "due", "") or "").strip()
        scheduled = str(get_field(t, "scheduled", "") or "").strip()
        priority = str(get_field(t, "priority", "") or "").strip().lower()

        if filter_type == "today":
            if due == today_str or scheduled == today_str:
                filtered.append(t)
        elif filter_type == "tomorrow":
            if due == tomorrow_str or scheduled == tomorrow_str:
                filtered.append(t)
        elif filter_type == "overdue":
            if (due and due < today_str) or (scheduled and scheduled < today_str):
                filtered.append(t)
        elif filter_type == "complete":
            if is_completed(t):
                filtered.append(t)
        elif filter_type == "archived":
            if is_archived(t):
                filtered.append(t)
        elif filter_type == "p1":
            if priority in ("high", "1", "p1", "highest"):
                filtered.append(t)
        elif filter_type == "p2":
            if priority in ("medium", "2", "p2", "normal"):
                filtered.append(t)
        elif filter_type == "p3":
            if priority in ("low", "3", "p3", "lowest"):
                filtered.append(t)

    return filtered


# -----------------------------
# Task search helpers
# -----------------------------
def _task_haystack(task: Any) -> str:
    """Build searchable text from task fields."""
    parts: List[str] = []
    for k in ("title", "path", "priority", "status", "due", "scheduled"):
        v = str(get_field(task, k, "") or "")
        if v:
            parts.append(v)

    tags = get_field(task, "tags", []) or []
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags if t)

    projects = get_field(task, "projects", []) or []
    if isinstance(projects, list):
        parts.extend(str(p) for p in projects if p)

    return _norm_title(" ".join(parts))


def _score_task(task: Any, q_norm: str, tokens: List[str], hay: str) -> Tuple[int, int, int, int, str]:
    """Score task relevance. Returns tuple for sorting (higher = better)."""
    title = str(get_field(task, "title", "") or "").strip()
    tnorm = _norm_title(title)

    exact = 1 if q_norm and tnorm == q_norm else 0
    starts = 1 if q_norm and tnorm.startswith(q_norm) else 0
    contains = 1 if q_norm and q_norm in tnorm else 0
    covered = sum(1 for tok in tokens if tok in hay)

    due = str(get_field(task, "due", "") or "").strip()
    due_sort = due if due else "9999-99-99"

    return (exact, starts, contains, covered, due_sort)


def _build_subtitle(task: Any, fields: List[str]) -> str:
    """Build subtitle string from specified task fields."""
    bits: List[str] = []

    for f in fields:
        if f == "tags":
            tags = get_field(task, "tags", []) or []
            if isinstance(tags, list) and tags:
                bits.append("Tags: " + ", ".join(str(t) for t in tags[:4]) + ("…" if len(tags) > 4 else ""))
            continue
        if f == "projects":
            projs = get_field(task, "projects", []) or []
            if isinstance(projs, list) and projs:
                # Strip Obsidian link brackets [[...]] for display
                clean_projs = [str(p).strip("[]") for p in projs[:3]]
                bits.append("Projects: " + ", ".join(clean_projs) + ("…" if len(projs) > 3 else ""))
            continue

        v = str(get_field(task, f, "") or "").strip()
        if v:
            # Use relative dates for due and scheduled fields
            if f in ("due", "scheduled"):
                v = _format_relative_date(v)
            bits.append(f"{f.capitalize()}: {v}")

    return " • ".join(bits) if bits else ""


# -----------------------------
# Obsidian launcher
# -----------------------------
def _launch_obsidian_best_effort() -> None:
    """Try to launch Obsidian (for API bootstrap)."""
    if not LAUNCH_OBSIDIAN_ON_ERROR:
        return
    try:
        subprocess.run(["open", "-a", "Obsidian"], check=False)
    except Exception:
        pass


# -----------------------------
# Time tracking (active session)
# -----------------------------
def _get_active_session_cached() -> Optional[Dict[str, Any]]:
    """Get active time tracking session (cached)."""
    cache = TimeSessionCache(ttl_seconds=TIME_ACTIVE_CACHE_TTL_SECONDS)

    # Check cache first
    cached_session = cache.get_cached_session()
    if cached_session is not None:
        return cached_session

    # Fetch fresh from API
    base = get_tasknotes_api_base()
    payload = http_get_json(f"{base}/time/active", timeout=2.0)
    active_norm: Optional[Dict[str, Any]] = None

    try:
        data = (payload or {}).get("data") or {}
        sessions = data.get("activeSessions") or []
        if isinstance(sessions, list) and sessions:
            first = sessions[0] if isinstance(sessions[0], dict) else {}
            task = first.get("task") or {}
            sess = first.get("session") or {}

            task_id = task.get("id") or ""
            title = task.get("title") or ""
            elapsed = first.get("elapsedMinutes")
            if elapsed is None:
                elapsed = sess.get("elapsedMinutes")

            active_norm = {
                "id": str(task_id or ""),
                "title": str(title or ""),
                "elapsedMinutes": int(elapsed) if isinstance(elapsed, (int, float)) else None,
                "tags": list(task.get("tags") or []) if isinstance(task.get("tags"), list) else [],
                "projects": list(task.get("projects") or []) if isinstance(task.get("projects"), list) else [],
                "priority": str(task.get("priority") or ""),
                "status": str(task.get("status") or ""),
            }
    except Exception:
        active_norm = None

    cache.save_session(active_norm)
    return active_norm


def _get_task_detail_cached(task_id: str) -> Optional[Dict[str, Any]]:
    """Fetch single task detail by ID (cached)."""
    if not task_id:
        return None

    cache = TaskDetailCache(ttl_seconds=TASK_DETAIL_CACHE_TTL_SECONDS)

    # Check cache first
    cached_task = cache.get_cached_task(task_id)
    if cached_task is not None:
        return cached_task

    # Fetch fresh from API
    base = get_tasknotes_api_base()
    enc_id = urllib.parse.quote(task_id, safe="")
    payload = http_get_json(f"{base}/tasks/{enc_id}", timeout=2.0)

    task = None
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict):
            task = payload.get("data")
        else:
            task = payload if "title" in payload and "path" in payload else None

    if isinstance(task, dict):
        cache.save_task(task_id, task)
        return task

    return None


# -----------------------------
# Create item builder
# -----------------------------
def _build_create_item(query: str, *, include_alt_switch: bool = False) -> Dict[str, Any]:
    """Build the 'Create task' Alfred item with modifiers."""
    parsed = parse_create_input(query, today=date.today())
    has_title = bool(parsed.title)

    meta = {
        "scheduled": parsed.scheduled,
        "due": parsed.due,
        "priority": parsed.priority,
        "tags": list(parsed.tags),
        "projects": list(parsed.projects),
        "details": getattr(parsed, "details", "") or "",
    }
    meta = {k: v for k, v in meta.items() if v}

    create_item = _alfred_item(
        f'Create: "{parsed.title}"' if has_title else "Create task (add a title)",
        ("Enter to create + notify" if has_title else "Type a title, then press Enter")
        + " • "
        + build_preview(parsed),
        valid=has_title,
        arg=json.dumps(
            {"action": "create", "text": parsed.title, "meta": meta, "raw": parsed.raw},
            ensure_ascii=False,
        )
        if has_title
        else "",
    )

    mods: Dict[str, Any] = {
        "cmd": {
            "subtitle": "⌘↩︎ Create + open" + (" • " + build_preview(parsed) if has_title else ""),
            "arg": json.dumps(
                {"action": "create", "text": parsed.title, "meta": meta, "raw": parsed.raw, "open": True},
                ensure_ascii=False,
            ),
            "valid": has_title,
        },
        "shift": {
            "subtitle": "⇧↩︎ Create verbatim (no NLP)",
            "arg": json.dumps({"action": "create", "text": parsed.raw, "verbatim": True}, ensure_ascii=False),
            "valid": bool(parsed.raw),
        },
        "cmd+shift": {
            "subtitle": "⇧⌘↩︎ Create verbatim + open",
            "arg": json.dumps({"action": "create", "text": parsed.raw, "verbatim": True, "open": True}, ensure_ascii=False),
            "valid": bool(parsed.raw),
        },
    }

    if include_alt_switch:
        mods["alt"] = {
            "subtitle": "⌥↩︎ Switch to search",
            "arg": parsed.raw,  # Plain string triggers external trigger
            "valid": bool(parsed.raw),
        }

    create_item["mods"] = mods
    return create_item


# -----------------------------
# Task list fetching with cache
# -----------------------------
def _fetch_tasks_with_cache(fetch_limit: int, *, include_completed: bool = False, include_archived: bool = False) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """
    Fetch tasks using cache with stale-while-revalidate pattern.

    Returns: (tasks, rerun_seconds or None)
    """
    import time

    cache = TaskCache(
        ttl_seconds=TASK_CACHE_TTL_SECONDS,
        max_stale_seconds=TASK_CACHE_MAX_STALE_SECONDS,
        refresh_backoff_seconds=TASK_CACHE_REFRESH_BACKOFF_SECONDS,
    )

    status = cache.get_cache_status()
    tasks = status["tasks"]
    age = status["age"]
    is_fresh = status["is_fresh"]
    is_usable = status["is_usable"]
    refresh_requested = status["refresh_requested"]
    should_fetch = status["should_fetch"]

    rerun: Optional[float] = None

    # Case 1: Cache is usable but stale, and no refresh requested yet
    if is_usable and not is_fresh and not refresh_requested:
        cache.mark_refresh_requested()
        return tasks, TASK_CACHE_RERUN_SECONDS

    # Case 2: Don't fetch (backoff period, use cache)
    if not should_fetch and is_usable:
        return tasks, None

    # Case 3: Fetch fresh data
    try:
        cache.mark_fetch_attempt()
        fresh_tasks = tn.list_tasks(
            limit=fetch_limit,
            completed=include_completed if include_completed else False,
            archived=include_archived if include_archived else False,
            sort="date_modified:desc",
        )
        tasks_raw = [tn.task_to_dict(t) for t in (fresh_tasks or [])]
        cache.mark_fetch_success(tasks_raw)
        return tasks_raw, None

    except Exception:
        if is_usable:
            return tasks, TASK_CACHE_RERUN_SECONDS
        else:
            # No cache, API failed - show loading state
            return [], None


# -----------------------------
# Task filtering and ranking
# -----------------------------
def _filter_and_rank_tasks(tasks: List[Dict[str, Any]], query: str, *, include_completed: bool = False, include_archived: bool = False) -> List[Dict[str, Any]]:
    """Filter tasks by query tokens and rank by relevance."""
    # Exclude completed/archived (unless explicitly including them)
    if include_archived:
        visible = [t for t in tasks if not is_completed(t)]
    elif include_completed:
        visible = [t for t in tasks if not is_archived(t)]
    else:
        visible = [t for t in tasks if not is_completed(t) and not is_archived(t)]

    if not query:
        return visible

    # Filter by token matching
    tokens = _tokenize(query)
    q_norm = _norm_title(query)

    filtered: List[Dict[str, Any]] = []
    for t in visible:
        hay = _task_haystack(t)
        if all(tok in hay for tok in tokens):
            filtered.append(t)

    # Score and rank
    ranked: List[Dict[str, Any]] = []
    for t in filtered:
        hay = _task_haystack(t)
        exact, starts, contains, covered, due_sort = _score_task(t, q_norm, tokens, hay)
        modified = str(get_field(t, "date_modified", "") or get_field(t, "date_created", "") or "")
        title = str(get_field(t, "title", "") or "")
        ranked.append({
            "task": t,
            "title_norm": _norm_title(title),
            "exact": exact,
            "starts": starts,
            "contains": contains,
            "covered": covered,
            "modified": modified,
            "due_sort": due_sort,
        })

    # Multi-key sort (stable sort chain)
    ranked.sort(key=lambda r: r["title_norm"])
    ranked.sort(key=lambda r: r["due_sort"])
    ranked.sort(key=lambda r: r["modified"], reverse=True)
    ranked.sort(key=lambda r: (r["exact"], r["starts"], r["contains"], r["covered"]), reverse=True)

    return [r["task"] for r in ranked]


# -----------------------------
# Tracked task pinning
# -----------------------------
def _ensure_tracked_task_pinned(
    tasks: List[Any],
    *,
    active: Optional[Dict[str, Any]],
    active_id: str,
    query: str,
) -> List[Any]:
    """Pin tracked task to top when query is empty."""
    if query or not active_id:
        return tasks

    # Find in list
    for i, t in enumerate(tasks):
        path = str(get_field(t, "path", "") or "").strip()
        if path == active_id:
            if i == 0:
                return tasks
            t = tasks.pop(i)
            tasks.insert(0, t)
            return tasks

    # Not found - fetch and inject
    fetched = _get_task_detail_cached(active_id)
    if isinstance(fetched, dict) and str(fetched.get("path") or "").strip():
        tasks.insert(0, fetched)
        return tasks

    # Fallback
    fallback: Dict[str, Any] = {
        "id": active_id,
        "path": active_id,
        "title": (active or {}).get("title") or "(Untitled task)",
        "tags": (active or {}).get("tags") or [],
        "projects": (active or {}).get("projects") or [],
        "priority": (active or {}).get("priority") or "",
        "status": (active or {}).get("status") or "",
    }
    tasks.insert(0, fallback)
    return tasks


# -----------------------------
# Task Alfred items builder
# -----------------------------
def _build_task_items(
    tasks: List[Dict[str, Any]],
    *,
    active_id: str,
    active_elapsed: Optional[int],
    subtitle_fields: List[str],
) -> List[Dict[str, Any]]:
    """Convert tasks to Alfred items with action menu and modifier shortcuts."""
    items: List[Dict[str, Any]] = []

    for t in tasks:
        title = str(get_field(t, "title", "") or "").strip() or "(Untitled task)"
        path = str(get_field(t, "path", "") or "").strip()
        subtitle = _build_subtitle(t, subtitle_fields)

        is_tracked = bool(active_id) and (path == active_id)
        if is_tracked:
            title = f"⏱ {title}"
            elapsed_txt = f"{active_elapsed}m" if isinstance(active_elapsed, int) else "active"
            subtitle = (f"Tracking: {elapsed_txt}" + (" • " + subtitle if subtitle else "")).strip()

        # Default action: open Task Actions menu (pass path, not JSON)
        item = _alfred_item(
            title,
            subtitle,
            valid=True,
            arg=path,  # Pass path to Task Actions Script Filter
        )

        # Modifier shortcuts bypass the action menu and execute directly
        ctrl_sub = "⌃↩︎ Stop tracking" if is_tracked else "⌃↩︎ Start tracking"

        item["mods"] = {
            "cmd": {
                "subtitle": "⌘↩︎ Open in Obsidian",
                "arg": json.dumps({"action": "open", "path": path}, ensure_ascii=False),
                "valid": True,
            },
            "shift": {
                "subtitle": "⇧↩︎ Toggle complete",
                "arg": json.dumps({"action": "toggle_complete", "path": path}, ensure_ascii=False),
                "valid": True,
            },
            "alt": {
                "subtitle": "⌥↩︎ Schedule for today",
                "arg": json.dumps({"action": "schedule_today", "path": path}, ensure_ascii=False),
                "valid": True,
            },
            "ctrl": {
                "subtitle": ctrl_sub,
                "arg": json.dumps({"action": "toggle_tracking", "path": path}, ensure_ascii=False),
                "valid": True,
            },
            "cmd+alt": {
                "subtitle": "⌥⌘↩︎ Delete task",
                "arg": json.dumps({"action": "delete", "path": path, "title": title.lstrip("⏱ ").strip()}, ensure_ascii=False),
                "valid": True,
            },
        }

        items.append(item)

    return items


# -----------------------------
# Mode handlers
# -----------------------------
def _handle_create_only_mode(query: str) -> int:
    """Handle create-only mode (triggered by > prefix)."""
    if not query:
        _alfred_output([
            _alfred_item(
                "Create task",
                'Type a title (use "//" for details). ↩=create • ⌘↩=open • ⇧↩=verbatim • Delete ">" to search',
                valid=False,
            )
        ])
        return 0

    create_item = _build_create_item(query, include_alt_switch=False)
    _alfred_output([create_item])
    return 0


def _handle_search_mode(query: str) -> int:
    """Handle search mode (main flow)."""
    fetch_limit = max(1, TASK_FETCH_LIMIT)
    return_limit = max(1, TASK_RETURN_LIMIT)
    subtitle_fields = _csv_fields(TASK_SUBTITLE_FIELDS)

    # Check for partial filter input (e.g., "!", "!to", "!ov") - show autocomplete suggestions
    if _is_partial_filter(query):
        suggestions = _build_filter_suggestion_items(query.strip())
        if suggestions:
            _alfred_output(suggestions)
            return 0
        # No matches - fall through to normal search (treats "!xyz" as a search term)

    # Parse quick filter prefix (e.g., "!today", "!overdue", "!p1", "!complete", "!archived")
    quick_filter, search_query = _parse_quick_filter(query)

    # Determine if we need to include completed or archived tasks
    include_completed = quick_filter == "complete"
    include_archived = quick_filter == "archived"

    # Fetch tasks
    tasks_raw, rerun = _fetch_tasks_with_cache(fetch_limit, include_completed=include_completed, include_archived=include_archived)

    # Handle API down, no cache
    if not tasks_raw and rerun is None:
        _launch_obsidian_best_effort()
        _alfred_output(
            [
                _alfred_item(
                    "Opening Obsidian…",
                    "TaskNotes API isn't ready yet. Keep Alfred open—I'll refresh automatically.",
                    valid=False,
                )
            ],
            rerun=1.0,
        )
        return 0

    # Apply quick filter first, then search within results
    filtered_tasks = tasks_raw
    if quick_filter:
        filtered_tasks = _apply_quick_filter(tasks_raw, quick_filter)

    # Check for exact title match (suppress create item if exists)
    norm_q = _norm_title(search_query) if search_query else ""
    existing_titles = {
        _norm_title(str(get_field(t, "title", "") or ""))
        for t in filtered_tasks
        if get_field(t, "title", "")
    }
    has_exact_title_match = bool(norm_q and norm_q in existing_titles)

    # Filter and rank using the remaining search query
    visible_sorted = _filter_and_rank_tasks(filtered_tasks, search_query, include_completed=include_completed, include_archived=include_archived)

    # Get active tracking session
    active = _get_active_session_cached()
    active_id = (active or {}).get("id") or ""
    active_elapsed = (active or {}).get("elapsedMinutes")

    # Pin tracked task when no search query (filter-only is ok)
    visible_sorted = _ensure_tracked_task_pinned(
        visible_sorted,
        active=active,
        active_id=active_id,
        query=search_query,
    )

    # Build create item (if applicable) - only for search queries, not filter-only
    create_item: Optional[Dict[str, Any]] = None
    if search_query and not has_exact_title_match:
        create_item = _build_create_item(search_query, include_alt_switch=False)

    # Cap task count to leave room for create item
    reserved = 1 if create_item else 0
    max_task_rows = max(0, return_limit - reserved)
    visible_sorted = visible_sorted[:max_task_rows]

    # Build task items
    task_items = _build_task_items(
        visible_sorted,
        active_id=active_id,
        active_elapsed=active_elapsed,
        subtitle_fields=subtitle_fields,
    )

    # Build filter label for empty state
    filter_labels = {
        "today": "today",
        "tomorrow": "tomorrow",
        "overdue": "overdue",
        "complete": "completed",
        "p1": "high priority",
        "p2": "medium priority",
        "p3": "low priority",
    }
    filter_label = filter_labels.get(quick_filter, "") if quick_filter else ""

    # Assemble output: create last when results exist, first when no results
    items: List[Dict[str, Any]] = []
    if not task_items:
        if create_item:
            items.append(create_item)
        elif not query:
            # Empty state with no query - show help
            items.append(_alfred_item(
                "No tasks yet",
                "Type to search or create a new task • ⌥J for Quick Create",
                valid=False,
            ))
        elif quick_filter and not search_query:
            # Filter-only with no results
            items.append(_alfred_item(
                f"No {filter_label} tasks",
                "Try a different filter or create a new task",
                valid=False,
            ))
        else:
            # Search returned no results
            display_query = search_query if search_query else query
            items.append(_alfred_item(
                f'No tasks matching "{display_query}"',
                "Try a different search or create a new task",
                valid=False,
            ))
    else:
        items.extend(task_items)
        if create_item:
            items.append(create_item)

    _alfred_output(items, rerun=rerun)
    return 0


# -----------------------------
# Main entry point
# -----------------------------
def main() -> int:
    """Main entry point for the script filter."""
    query = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    mode = (os.environ.get("MODE") or "").strip().lower()

    # Prefix-based mode: ">" switches to create-focused mode
    if query.startswith(">"):
        return _handle_create_only_mode(query[1:].strip())

    # Legacy MODE env var support (for backwards compatibility)
    if mode == "create_only":
        return _handle_create_only_mode(query)

    return _handle_search_mode(query)


if __name__ == "__main__":
    raise SystemExit(main())
