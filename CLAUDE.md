# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TaskNotes is an Alfred workflow for macOS that integrates with the TaskNotes plugin for Obsidian. It provides quick task search, creation with natural language parsing, and time tracking capabilities.

## Architecture

### Entry Points (Alfred triggers)

- **Hotkey ⌥J** or keyword `tn`: Opens the TaskNotes script filter

### Mode Switching

The workflow uses a prefix-based mode system:
- Normal query → Search mode (shows matching tasks)
- Query starting with `>` → Create mode (create-focused UI with NLP parsing)
- Query starting with `>>` → Pomodoro mode (start/control pomodoro timer)

### Alfred Workflow Structure

The workflow is defined in `info.plist` which contains:
- Hotkey trigger (⌥J)
- Keyword trigger (`tn`)
- External trigger (`main`) - Used for "Go Back" from Task Actions
- Script Filter (runs `list_or_parse_task.py`)
- Conditional (routes JSON actions vs task paths)
- Task Actions Script Filter (runs `task_actions.py`)
- Run Script action (runs `open_or_create_task.py`)

Flow diagram:
```
⌥J / tn → Script Filter → Conditional
                              ├── JSON (modifiers) → Run Script
                              └── Task Path (Enter) → Task Actions → Run Script

External Trigger (main) → Script Filter (for "Go Back")
```

### Core Python Modules

1. **`src/list_or_parse_task.py`** - Main Alfred Script Filter
   - Detects `>` prefix to switch between search and create modes
   - Fetches tasks from TaskNotes API with caching (TTL-based, stale-while-revalidate pattern)
   - Ranks/filters tasks locally with token-based matching
   - Supports quick filters (!today, !tomorrow, !overdue, !complete, !archived, !p1, !p2, !p3)
   - Outputs Alfred JSON with task items and modifiers
   - Pins actively tracked task to top when query is empty
   - Pins pomodoro status to top when a session is running (with stale fallback when offline)

2. **`src/open_or_create_task.py`** - Action Handler
   - Receives JSON payload with `action` field:
     - `create` - Create new task
     - `open` - Open task in Obsidian
     - `toggle_tracking` / `toggle_tracking_open` - Start/stop time tracking
     - `stop_tracking` - Stop tracking
     - `toggle_complete` - Mark task done/reopen
     - `schedule_today` - Set scheduled date to today
     - `delete` - Move task to trash
     - `toggle_archive` - Archive/unarchive task
     - `go_back` - Trigger external trigger to return to task list
     - `start_pomodoro` - Start pomodoro session (optionally with task)
     - `stop_pomodoro` - Stop pomodoro session
     - `pause_pomodoro` - Pause pomodoro timer
     - `resume_pomodoro` - Resume paused pomodoro
     - `open_pomodoro_controls` - Open pomodoro mode (>>)
     - `open_pomodoro_view` - Open TaskNotes pomodoro timer view in Obsidian (via Advanced URI)
   - Creates tasks via TaskNotes API, opens tasks in Obsidian via URI scheme
   - Manages single-active-session time tracking
   - Auto-launches Obsidian if API unreachable (background for notify, foreground for open)

3. **`src/task_actions.py`** - Task Actions Menu
   - Displays action menu for a selected task
   - Task title header opens task in Obsidian
   - Lists available actions: Time Tracking, Schedule Today, Complete/Reopen, Archive/Unarchive, Delete, Go Back
   - Fetches task details and tracking state from API

4. **`src/nlp_task_create.py`** - Natural Language Date/Metadata Parser
   - Parses quick-add strings into structured task metadata
   - Supports: dates (today, tomorrow, weekdays, ISO, US format, relative offsets like "in 2 weeks")
   - Supports: priority (p1/p2/p3), tags (#tag), projects (+Project Name), details (after `//`, with `\n` for newlines—spaces around `\n` are trimmed)
   - Bare dates default to scheduled; explicit keywords (`due`, `by`) set due date

5. **`src/tasknotes_alfred.py`** - TaskNotes API Client
   - HTTP helpers for TaskNotes REST API (GET/POST with Bearer token auth)
   - Task model normalization (dataclass `Task`)
   - Pomodoro API helpers (`get_pomodoro_status`, `start_pomodoro`, `stop_pomodoro`, `pause_pomodoro`, `resume_pomodoro`)
   - Graceful fallback for older API versions

6. **`src/cache.py`** - Cache Management
   - TaskCache: Main task list with TTL and stale-while-revalidate
   - TimeSessionCache: Active time tracking session (available but not currently used)
   - TaskDetailCache: Single task details for tracked task injection (available but not currently used)
   - PomodoroCache: Pomodoro status cache for pinned display (supports stale fallback when Obsidian is closed)

7. **`src/utils.py`** - Shared Utilities
   - `get_emoji_icon_path()`: Look up bundled PNG icons for Alfred items
   - `http_get_json()`: HTTP GET with TaskNotes authentication
   - `get_field()`, `is_completed()`, `is_archived()`: Task field accessors
   - `get_workflow_icon_path()`: Get path to workflow icon
   - Configuration helpers: `get_tasknotes_api_base()`, `get_tasknotes_token()`

### Data Flow

```
Alfred trigger → list_or_parse_task.py (Script Filter JSON)
              → User selects item
              → Conditional checks if JSON or task path
                  ├── JSON (modifier action) → open_or_create_task.py → TaskNotes API
                  └── Task path (Enter) → task_actions.py → User selects action
                                        → open_or_create_task.py → TaskNotes API
```

### Modifier Keys (Script Filter Items)

Task items have two behaviors:
- `↩` (plain Enter) - Opens Task Actions menu (task_actions.py)
- Modifier keys bypass the menu and execute actions directly:
  - `⌘↩` - Open in Obsidian
  - `⇧↩` - Toggle complete
  - `⌥↩` - Schedule for today
  - `⌃↩` - Toggle time tracking
  - `⌥⌘↩` - Delete task

Create items support:
- `↩` - Create + notify
- `⌘↩` - Create + open
- `⇧↩` - Create verbatim
- `⇧⌘↩` - Create verbatim + open

Pinned pomodoro status (when a pomodoro is running):
- `↩` - Open pomodoro controls (`>>` mode)
- `⌘↩` - Open task in Obsidian (if linked) or pomodoro timer view
- `⌥↩` - Pause/Resume pomodoro
- `⌃↩` - Stop pomodoro

## Configuration (Environment Variables)

Set in Alfred workflow variables (info.plist):

| Variable | Purpose |
|----------|---------|
| `TASKNOTES_API_BASE` | API endpoint (default: `http://localhost:8080/api`) |
| `TASKNOTES_TOKEN` | Bearer token for API auth |
| `OBSIDIAN_VAULT` | Vault name for Obsidian URI |
| `OBSIDIAN_VAULT_ID` | Stable vault ID (preferred over name) |
| `TASK_FETCH_LIMIT` | Max tasks to fetch from API (400) |
| `TASK_RETURN_LIMIT` | Max items shown in Alfred (50) |
| `TASK_CACHE_TTL_SECONDS` | Cache freshness (5) |

## Testing

No formal test framework. Manual testing via:

```bash
# Test NLP parser
python3 src/nlp_task_create.py

# Test task listing (requires running TaskNotes API)
python3 src/tasknotes_alfred.py "search query"
```

## TaskNotes API Reference

The workflow interacts with TaskNotes HTTP API endpoints:
- `GET /api/tasks` - List tasks with query params (limit, completed, archived, sort)
- `POST /api/tasks` - Create task
- `GET /api/tasks/{id}` - Get task by path
- `PUT /api/tasks/{id}` - Update task (partial updates)
- `DELETE /api/tasks/{id}` - Delete task
- `POST /api/tasks/{id}/toggle-status` - Toggle complete/open status
- `POST /api/tasks/{id}/archive` - Toggle archive status
- `GET /api/time/active` - Get active tracking sessions
- `POST /api/tasks/{id}/time/start` - Start tracking
- `POST /api/tasks/{id}/time/stop` - Stop tracking
- `GET /api/pomodoro/status` - Get pomodoro status (isRunning, timeRemaining, isPaused, etc.)
- `POST /api/pomodoro/start` - Start pomodoro (optional taskId in body)
- `POST /api/pomodoro/stop` - Stop pomodoro
- `POST /api/pomodoro/pause` - Pause pomodoro
- `POST /api/pomodoro/resume` - Resume paused pomodoro
- `GET /api/health` - Health check

See `TaskNotes API/TaskNotes HTTP API.md` for full API documentation.
