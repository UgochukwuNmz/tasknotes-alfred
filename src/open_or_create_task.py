#!/usr/bin/env python3
"""Open or create TaskNotes tasks (Alfred action script).

Receives a JSON payload from the Script Filter (list_or_parse_task.py) and:
- creates a TaskNotes task (optionally with NLP-parsed metadata), then notifies
- opens an existing task by path
- optionally opens the created task in Obsidian
- optionally writes rich body content into the task note (below YAML frontmatter)
- toggles TaskNotes time tracking for a task (single active session)

Enhancement:
- If Obsidian is closed (TaskNotes API not reachable), auto-launch Obsidian
  (background or foreground depending on action), wait for /api/health to be ok,
  then proceed with create/track/stop.

Requested behavior:
- Open in background for create + notify (Enter)
- Open in foreground for create + open (Cmd+Enter)
- Track in background for track + notify (Opt+Enter)
- Track in foreground for track + open (Opt+Cmd+Enter)
- stop_tracking follows same model as start_tracking
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from tasknotes_alfred import APIError, TASKNOTES_API_BASE, TASKNOTES_TOKEN, create_task


# -----------------------------
# Config
# -----------------------------
# How long we wait for Obsidian + TaskNotes API to become healthy after we launch it.
TASKNOTES_STARTUP_WAIT_SECONDS = float(os.environ.get("TASKNOTES_STARTUP_WAIT_SECONDS", "12").strip() or "12")
TASKNOTES_HEALTH_POLL_INTERVAL_SECONDS = float(
    os.environ.get("TASKNOTES_HEALTH_POLL_INTERVAL_SECONDS", "0.25").strip() or "0.25"
)
AUTO_START_OBSIDIAN_FOR_API = (os.environ.get("AUTO_START_OBSIDIAN_FOR_API", "1").strip() == "1")

# Whether we show "Opening Obsidian..." notifications when bootstrapping.
BOOTSTRAP_NOTIFY = (os.environ.get("TASKNOTES_BOOTSTRAP_NOTIFY", "1").strip() == "1")


# -----------------------------
# Helpers
# -----------------------------
def _project_links(projects: Any) -> List[str]:
    """TaskNotes expects projects as Obsidian links (e.g. [[Work]])."""
    out: List[str] = []
    for p in projects or []:
        s = str(p).strip()
        if not s:
            continue
        if s.startswith("[[") and s.endswith("]]"):
            out.append(s)
        else:
            out.append(f"[[{s}]]")
    return out


def notify(title: str, message: str) -> None:
    """Send a macOS notification (best effort)."""
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False)


def open_in_obsidian(vault_identifier: str, vault_relative_path: str) -> None:
    """Open a file in Obsidian via URI."""
    file_param = (vault_relative_path or "").lstrip("/")

    # Obsidian allows omitting the .md extension.
    if file_param.lower().endswith(".md"):
        file_param = file_param[:-3]

    url = (
        "obsidian://open"
        f"?vault={quote(vault_identifier, safe='')}"
        f"&file={quote(file_param, safe='')}"
    )
    subprocess.run(["open", url], check=False)


def _launch_obsidian(launch_mode: str) -> None:
    """Best-effort launch Obsidian. launch_mode: 'background' | 'foreground'."""
    try:
        if launch_mode == "background":
            # -g: do not bring the application to the foreground (best effort)
            subprocess.run(["open", "-g", "-a", "Obsidian"], check=False)
        else:
            subprocess.run(["open", "-a", "Obsidian"], check=False)
            # Ensure focus in case macOS doesn't foreground it reliably.
            subprocess.run(["osascript", "-e", 'tell application "Obsidian" to activate'], check=False)
    except Exception:
        pass


# -----------------------------
# TaskNotes HTTP helpers
# -----------------------------
def _tasknotes_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if TASKNOTES_TOKEN:
        headers["Authorization"] = f"Bearer {TASKNOTES_TOKEN}"
    return headers


def _tasknotes_request_json(
    method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 5.0
) -> Dict[str, Any]:
    url = f"{TASKNOTES_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=_tasknotes_headers())

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.URLError as e:
        raise APIError(f"Failed to connect to TaskNotes API at {TASKNOTES_API_BASE}: {e}") from e
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise APIError(f"TaskNotes API error ({e.code}): {raw[:500]}") from e
    except json.JSONDecodeError as e:
        raise APIError("Non-JSON response from TaskNotes API") from e


def _tasknotes_health_ok(timeout_seconds: float = 0.8) -> bool:
    """Return True if TaskNotes API health endpoint responds with success/ok."""
    url = f"{TASKNOTES_API_BASE.rstrip('/')}/health"
    req = urllib.request.Request(url, method="GET", headers=_tasknotes_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict) and payload.get("success") is True:
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            return data.get("status") == "ok"
    except Exception:
        return False
    return False


def _ensure_tasknotes_running(launch_mode: str) -> bool:
    """
    If API is down, (optionally) launch Obsidian (background/foreground) and wait
    briefly for /health to return ok.
    """
    if _tasknotes_health_ok():
        return True

    if not AUTO_START_OBSIDIAN_FOR_API:
        return False

    _launch_obsidian(launch_mode)

    deadline = time.monotonic() + max(0.0, TASKNOTES_STARTUP_WAIT_SECONDS)
    while time.monotonic() < deadline:
        if _tasknotes_health_ok(timeout_seconds=0.8):
            return True
        time.sleep(max(0.05, TASKNOTES_HEALTH_POLL_INTERVAL_SECONDS))

    return False


def _with_tasknotes_ready(fn, *, launch_mode: str, purpose: str):
    """
    Run fn(). If the API is unreachable (Obsidian closed / TaskNotes server not up),
    launch Obsidian using launch_mode, wait for health, then retry once.
    """
    try:
        return fn()
    except APIError as e:
        # If health is already OK, this is likely a real API error (400/500/etc). Don't relaunch.
        if _tasknotes_health_ok():
            raise

        if BOOTSTRAP_NOTIFY and purpose:
            notify("TaskNotes", f"Opening Obsidian to {purpose}…")

        ok = _ensure_tasknotes_running(launch_mode)
        if not ok:
            raise APIError(
                "TaskNotes API is not running yet. If Obsidian opened to a vault picker, select a vault and try again."
            ) from e

        return fn()


# -----------------------------
# Time tracking (single active session)
# -----------------------------
def _get_active_sessions(*, launch_mode: str) -> List[Dict[str, Any]]:
    payload = _with_tasknotes_ready(
        lambda: _tasknotes_request_json("GET", "/time/active", None, timeout=3.0),
        launch_mode=launch_mode,
        purpose="start TaskNotes API",
    )
    data = (payload or {}).get("data") or {}
    sessions = data.get("activeSessions") or []
    return sessions if isinstance(sessions, list) else []


def _stop_tracking(task_id: str, *, launch_mode: str) -> None:
    enc = urllib.parse.quote(task_id, safe="")
    _with_tasknotes_ready(
        lambda: _tasknotes_request_json("POST", f"/tasks/{enc}/time/stop", None, timeout=5.0),
        launch_mode=launch_mode,
        purpose="stop tracking",
    )


def _start_tracking(task_id: str, *, launch_mode: str) -> None:
    enc = urllib.parse.quote(task_id, safe="")
    _with_tasknotes_ready(
        lambda: _tasknotes_request_json("POST", f"/tasks/{enc}/time/start", None, timeout=5.0),
        launch_mode=launch_mode,
        purpose="start tracking",
    )


def _toggle_tracking_single_session(task_id: str, *, launch_mode: str) -> str:
    """
    Enforces single active session:
      - If task_id is active -> stop it
      - Else stop any other active session (if present), then start task_id
    Returns: "started" | "stopped"
    """
    sessions = _get_active_sessions(launch_mode=launch_mode)
    active_task_id = ""
    if sessions and isinstance(sessions[0], dict):
        task = (sessions[0].get("task") or {}) if isinstance(sessions[0].get("task"), dict) else {}
        active_task_id = str(task.get("id") or "")

    if active_task_id and active_task_id == task_id:
        _stop_tracking(task_id, launch_mode=launch_mode)
        return "stopped"

    if active_task_id and active_task_id != task_id:
        _stop_tracking(active_task_id, launch_mode=launch_mode)

    _start_tracking(task_id, launch_mode=launch_mode)
    return "started"


# -----------------------------
# Vault path resolution (for writing body)
# -----------------------------
def _obsidian_config_candidates() -> List[Path]:
    """Common Obsidian config locations on macOS."""
    home = Path.home()
    return [
        home / "Library" / "Application Support" / "obsidian" / "obsidian.json",
        home / "Library" / "Application Support" / "Obsidian" / "obsidian.json",
    ]


def _resolve_vault_root(vault_id: str, vault_name: str) -> Optional[Path]:
    """Best-effort resolve the vault root path."""
    explicit = (os.environ.get("OBSIDIAN_VAULT_PATH") or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists() and p.is_dir():
            return p

    for candidate in _obsidian_config_candidates():
        try:
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            vaults = data.get("vaults") or {}
            if isinstance(vaults, dict):
                if vault_id and vault_id in vaults and isinstance(vaults[vault_id], dict):
                    path = vaults[vault_id].get("path")
                    if path:
                        p = Path(path).expanduser()
                        if p.exists() and p.is_dir():
                            return p
                if vault_name:
                    for _, v in vaults.items():
                        if isinstance(v, dict):
                            path = v.get("path")
                            if not path:
                                continue
                            p = Path(path).expanduser()
                            if p.name == vault_name and p.exists() and p.is_dir():
                                return p
        except Exception:
            continue

    return None


def _safe_task_file_path(vault_root: Path, vault_relative_path: str) -> Optional[Path]:
    """Return an absolute file path, ensuring it stays inside the vault."""
    rel = (vault_relative_path or "").lstrip("/")
    if not rel:
        return None

    if not rel.lower().endswith(".md"):
        rel = rel + ".md"

    root = vault_root.expanduser().resolve()
    abs_path = (root / rel).resolve()

    try:
        abs_path.relative_to(root)
    except Exception:
        return None

    return abs_path


def _insert_body_below_frontmatter(text: str, body: str) -> str:
    """Insert body content below YAML frontmatter if present."""
    if not body.strip():
        return text

    if text.startswith("---\n"):
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            before = parts[0] + "\n---\n"
            after = parts[1]
            if after.strip() == "":
                return before + "\n" + body
            sep = "\n\n" if not after.endswith("\n") else "\n"
            return before + after + sep + body

    sep = "\n\n" if not text.endswith("\n") else "\n"
    return text + sep + body


def _write_details_to_note_body(vault_root: Path, vault_relative_path: str, details: str) -> bool:
    """Best-effort write details into the markdown body if it's currently empty."""
    path = _safe_task_file_path(vault_root, vault_relative_path)
    if not path or not path.exists():
        return False

    try:
        original = path.read_text(encoding="utf-8")
    except Exception:
        return False

    body_only = original
    if original.startswith("---\n"):
        parts = original.split("\n---\n", 1)
        if len(parts) == 2:
            body_only = parts[1]
    if body_only.strip():
        return False

    updated = _insert_body_below_frontmatter(original, details)
    if updated == original:
        return False

    try:
        path.write_text(updated, encoding="utf-8")
        return True
    except Exception:
        return False


# -----------------------------
# Main
# -----------------------------
def main() -> None:
    payload = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    payload = (payload or "").strip()
    if not payload:
        return

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        notify("TaskNotes", f"Invalid input: {str(e)[:100]}")
        return

    action = data.get("action")

    if action == "create":
        text = (data.get("text") or "").strip()
        if not text:
            notify("TaskNotes", "No title provided.")
            return

        verbatim = bool(data.get("verbatim"))
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}

        details = ""
        if isinstance(meta.get("details"), str):
            details = meta.get("details", "")
        elif isinstance(data.get("details"), str):
            details = data.get("details", "")
        details = (details or "").strip()

        # Requested: background for create+notify, foreground for create+open
        launch_mode = "foreground" if data.get("open") else "background"

        def _do_create():
            if verbatim or not meta:
                return create_task(text)
            return create_task(
                text,
                due=meta.get("due") or None,
                scheduled=meta.get("scheduled") or None,
                priority=meta.get("priority") or None,
                tags=meta.get("tags") or None,
                projects=_project_links(meta.get("projects") or None),
                details=details or None,
            )

        try:
            task = _with_tasknotes_ready(_do_create, launch_mode=launch_mode, purpose="create task")
        except APIError as e:
            notify("TaskNotes", f"Create failed: {str(e)}")
            return

        notify("Task created", task.title or text)

        if details:
            vault_id = (os.environ.get("OBSIDIAN_VAULT_ID") or "").strip()
            vault_name = (os.environ.get("OBSIDIAN_VAULT") or "").strip()
            vault_root = _resolve_vault_root(vault_id=vault_id, vault_name=vault_name)
            if vault_root:
                _write_details_to_note_body(vault_root, task.path, details)

        if data.get("open"):
            vault_id = (os.environ.get("OBSIDIAN_VAULT_ID") or "").strip()
            vault_name = (os.environ.get("OBSIDIAN_VAULT") or "").strip()
            vault_identifier = vault_id or vault_name
            if not vault_identifier:
                notify(
                    "TaskNotes",
                    "Set workflow env var OBSIDIAN_VAULT (name) or OBSIDIAN_VAULT_ID (stable id) to enable opening.",
                )
                return
            open_in_obsidian(vault_identifier, task.path)

        return

    if action in {"toggle_tracking", "toggle_tracking_open", "stop_tracking"}:
        task_id = (data.get("path") or "").strip()
        if not task_id:
            return

        # Requested: background for track+notify, foreground for track+open
        launch_mode = "foreground" if action == "toggle_tracking_open" else "background"

        try:
            if action == "stop_tracking":
                sessions = _get_active_sessions(launch_mode=launch_mode)
                active_task_id = ""
                if sessions and isinstance(sessions[0], dict):
                    task = (sessions[0].get("task") or {}) if isinstance(sessions[0].get("task"), dict) else {}
                    active_task_id = str(task.get("id") or "")
                if active_task_id and active_task_id == task_id:
                    _stop_tracking(task_id, launch_mode=launch_mode)
                    notify("Tracking stopped", "Stopped active time tracking.")
                return

            result = _toggle_tracking_single_session(task_id, launch_mode=launch_mode)
            if result == "started":
                notify("Tracking started", "Now tracking this task.")
            else:
                notify("Tracking stopped", "Stopped tracking this task.")
        except APIError as e:
            notify("TaskNotes", f"Tracking failed: {str(e)}")
            return

        if action == "toggle_tracking_open":
            vault_id = (os.environ.get("OBSIDIAN_VAULT_ID") or "").strip()
            vault_name = (os.environ.get("OBSIDIAN_VAULT") or "").strip()
            vault_identifier = vault_id or vault_name
            if vault_identifier:
                open_in_obsidian(vault_identifier, task_id)

        return

    # Important: "open existing task" behavior remains unchanged.
    if action == "open":
        vault_id = (os.environ.get("OBSIDIAN_VAULT_ID") or "").strip()
        vault_name = (os.environ.get("OBSIDIAN_VAULT") or "").strip()
        vault_identifier = vault_id or vault_name
        if not vault_identifier:
            notify(
                "TaskNotes",
                "Set workflow env var OBSIDIAN_VAULT (name) or OBSIDIAN_VAULT_ID (stable id) to enable opening.",
            )
            return

        path = (data.get("path") or "").strip()
        if not path:
            return

        open_in_obsidian(vault_identifier, path)
        return

    if action == "delete":
        task_id = (data.get("path") or "").strip()
        task_title = (data.get("title") or "this task").strip()
        if not task_id:
            return

        # Delete via API
        try:
            enc_id = urllib.parse.quote(task_id, safe="")
            _with_tasknotes_ready(
                lambda: _tasknotes_request_json("DELETE", f"/tasks/{enc_id}", None, timeout=5.0),
                launch_mode="background",
                purpose="delete task",
            )
            notify("Task deleted", f'"{task_title}" moved to trash.')
        except APIError as e:
            notify("TaskNotes", f"Delete failed: {str(e)}")
        return

    if action == "toggle_complete":
        task_id = (data.get("path") or "").strip()
        if not task_id:
            return

        try:
            enc_id = urllib.parse.quote(task_id, safe="")
            result = _with_tasknotes_ready(
                lambda: _tasknotes_request_json("POST", f"/tasks/{enc_id}/toggle-status", None, timeout=5.0),
                launch_mode="background",
                purpose="toggle task status",
            )
            # Check new status from response
            task_data = (result or {}).get("data", {})
            new_status = task_data.get("status", "").lower()
            if new_status in ("done", "completed", "complete"):
                notify("Task completed", "✓ Marked as done")
            else:
                notify("Task reopened", "Marked as open")
        except APIError as e:
            notify("TaskNotes", f"Toggle failed: {str(e)}")
        return

    if action == "toggle_schedule":
        task_id = (data.get("path") or "").strip()
        if not task_id:
            return

        try:
            enc_id = urllib.parse.quote(task_id, safe="")

            # Fetch current task to check scheduled date
            task_data = _with_tasknotes_ready(
                lambda: _tasknotes_request_json("GET", f"/tasks/{enc_id}", None, timeout=5.0),
                launch_mode="background",
                purpose="fetch task",
            )
            current_scheduled = str((task_data or {}).get("data", {}).get("scheduled", "") or "").strip()

            if current_scheduled:
                # Clear schedule
                _with_tasknotes_ready(
                    lambda: _tasknotes_request_json("PUT", f"/tasks/{enc_id}", {"scheduled": None}, timeout=5.0),
                    launch_mode="background",
                    purpose="clear schedule",
                )
                notify("Schedule cleared", "🗓️ Removed scheduled date")
            else:
                # Schedule for today
                today_str = date.today().isoformat()
                _with_tasknotes_ready(
                    lambda: _tasknotes_request_json("PUT", f"/tasks/{enc_id}", {"scheduled": today_str}, timeout=5.0),
                    launch_mode="background",
                    purpose="schedule task",
                )
                notify("Task scheduled", f"📅 Scheduled for today ({today_str})")
        except APIError as e:
            notify("TaskNotes", f"Schedule failed: {str(e)}")
        return

    if action == "toggle_archive":
        task_id = (data.get("path") or "").strip()
        if not task_id:
            return

        try:
            enc_id = urllib.parse.quote(task_id, safe="")
            result = _with_tasknotes_ready(
                lambda: _tasknotes_request_json("POST", f"/tasks/{enc_id}/archive", None, timeout=5.0),
                launch_mode="background",
                purpose="toggle archive",
            )
            # Check new archived state from response
            task_data = (result or {}).get("data", {})
            is_archived = task_data.get("archived", False)
            if is_archived:
                notify("Task archived", "📦 Moved to archive")
            else:
                notify("Task unarchived", "📤 Restored from archive")
        except APIError as e:
            notify("TaskNotes", f"Archive failed: {str(e)}")
        return

    if action == "go_back":
        # Trigger external trigger to reopen TaskNotes main view
        bundle_id = os.environ.get("alfred_workflow_bundleid", "com.emmanuelihim.tasknotes")
        script = f'tell application id "com.runningwithcrayons.Alfred" to run trigger "main" in workflow "{bundle_id}"'
        subprocess.run(["osascript", "-e", script], check=False)
        return


if __name__ == "__main__":
    main()
