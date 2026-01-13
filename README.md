# TaskNotes Alfred Workflow

A powerful Alfred workflow for managing tasks in [TaskNotes](https://tasknotes.dev), the task management plugin for Obsidian. Search, create, track time, and manage tasks without leaving your keyboard.

## Features

- **Quick Search** - Instantly find tasks with fuzzy matching
- **Natural Language Creation** - Create tasks with dates, priorities, tags, and projects using plain English
- **Time Tracking** - Start/stop time tracking with a single keystroke
- **Quick Filters** - Filter by today, tomorrow, overdue, or priority
- **Relative Dates** - See "Today", "Tomorrow", "Wed" instead of raw dates
- **Full Task Management** - Complete, schedule, delete tasks directly from Alfred

## Requirements

- [Alfred 5](https://www.alfredapp.com/) with Powerpack license
- [Obsidian](https://obsidian.md/)
- [TaskNotes plugin](https://tasknotes.dev) with HTTP API enabled
- Python 3.8+

## Installation

1. Download the workflow and double-click to install
2. In Obsidian, enable TaskNotes HTTP API:
   - Open TaskNotes Settings → HTTP API tab
   - Enable the API and set your preferred port (default: 8080)
   - Optionally set an authentication token
   - Restart Obsidian
3. Configure workflow environment variables (see [Configuration](#configuration))

## Usage

### Opening the Workflow

| Shortcut | Action |
|----------|--------|
| `⌥J` | Open TaskNotes |
| `tn` | Type in Alfred to open TaskNotes |

### Modes

TaskNotes has two modes, controlled by the `>` prefix:

| You type | Mode | Behavior |
|----------|------|----------|
| `meeting` | Search | Shows matching tasks, create option at bottom |
| `>meeting tomorrow p1` | Create | Create-focused mode with NLP parsing |

To switch from create to search, just delete the `>` prefix.

### Task Actions

When you select a task and press Enter, a **Task Actions menu** appears with the following options:

| Action | Description |
|--------|-------------|
| *[Task Title]* | Open the task in Obsidian |
| Start/Stop Time Tracking | Toggle time tracking for this task |
| Schedule for Today | Set the scheduled date to today |
| Complete Task / Reopen Task | Toggle task completion status |
| Archive Task / Unarchive Task | Move to/from archive |
| Delete Task | Move task to trash |
| Go Back | Return to the task list |

### Quick Actions (Modifier Keys)

For faster access, use modifier keys with Enter to bypass the action menu:

| Shortcut | Action |
|----------|--------|
| `↩` | Open Task Actions menu |
| `⌘↩` | Open task in Obsidian |
| `⇧↩` | Toggle complete (mark done/reopen) |
| `⌥↩` | Schedule task for today |
| `⌃↩` | Toggle time tracking |
| `⌥⌘↩` | Delete task (move to trash) |

### Creating Tasks

When in create mode (query starts with `>`), use these modifier keys:

| Shortcut | Action |
|----------|--------|
| `↩` | Create task + show notification |
| `⌘↩` | Create task + open in Obsidian |
| `⇧↩` | Create verbatim (skip NLP parsing) |
| `⇧⌘↩` | Create verbatim + open in Obsidian |

### Quick Filters

Type `!` to see all available filters with autocomplete:

| Filter | Description |
|--------|-------------|
| 📅 `!today` | Tasks due or scheduled for today |
| 🗓️ `!tomorrow` | Tasks due or scheduled for tomorrow |
| ⚠️ `!overdue` | Tasks past their due or scheduled date |
| ✅ `!complete` | Completed tasks |
| 📦 `!archived` | Archived tasks |
| 🔴 `!p1` | High priority tasks |
| 🟡 `!p2` | Medium priority tasks |
| 🟢 `!p3` | Low priority tasks |

**Autocomplete**: Type `!` and press **Tab** on any suggestion to apply it. Continue typing to narrow down options (e.g., `!to` shows only Today and Tomorrow).

Combine filters with search: `!today meeting` shows today's tasks containing "meeting".

### Search Ranking

When you search, tasks are filtered and ranked as follows:

**Filtering**: A task matches only if *all* search tokens appear somewhere in its searchable fields (title, path, priority, status, due date, scheduled date, tags, or projects).

**Ranking**: Matching tasks are scored and sorted by:

| Priority | Factor | Description |
|----------|--------|-------------|
| 1st | Exact title match | Title exactly equals your query |
| 2nd | Title starts with | Title begins with your query |
| 3rd | Title contains | Query appears within the title |
| 4th | Token coverage | Number of search tokens found |
| 5th | Recently modified | More recently modified tasks first |
| 6th | Due date | Earlier due dates first |
| 7th | Alphabetical | By title (baseline tiebreaker) |

**Special behavior**:
- When query is empty, the actively tracked task is pinned to the top
- Quick filters (`!today`, etc.) are applied before ranking

## Natural Language Task Creation

Create tasks with metadata using natural language. The parser understands:

### Dates

| Input | Result |
|-------|--------|
| `today`, `tomorrow`, `yesterday` | Relative dates |
| `monday`, `tue`, `friday` | Next occurrence of weekday |
| `jan 15`, `march 3rd` | Specific dates |
| `2025-01-15` | ISO format dates |
| `1/15`, `01/15/2025` | US format dates |
| `in 2 days`, `in 3 weeks` | Relative offsets |
| `next week`, `next month` | Relative periods |

### Date Keywords

| Keyword | Sets Field |
|---------|------------|
| `due`, `by` | Due date |
| `sch`, `on`, `scheduled`, `start` | Scheduled date |
| *(bare date)* | Scheduled date (default) |

### Priority

| Input | Priority |
|-------|----------|
| `p1`, `!!!` | High |
| `p2`, `!!` | Medium |
| `p3`, `!` | Low |

### Tags and Projects

| Syntax | Example |
|--------|---------|
| `#tag` | `#work`, `#urgent` |
| `+Project` | `+Work`, `+Personal` |
| `+Project Name` | `+Q1 Planning` (multi-word with space after +) |

### Details

Use `//` to add details/notes to a task:

```
Buy groceries tomorrow // milk, eggs, bread
```

For multi-line details, use `\n` or double spaces:

```
Meeting notes // Point 1 \n Point 2 \n Point 3
Shopping list // eggs  milk  bread
```

Spaces around `\n` are automatically trimmed, so `line 1 \n line 2` produces clean lines without leading/trailing spaces.

### Examples

```
Call dentist tomorrow p1
Review report due friday #work +Project Alpha
Team meeting on monday 2pm // Discuss Q1 goals
Submit expenses by jan 15 p2 #finance
```

## Configuration

Set these environment variables in the Alfred workflow settings (click the `[x]` icon in the workflow editor):

### Required

| Variable | Description | Default |
|----------|-------------|---------|
| `OBSIDIAN_VAULT` | Your Obsidian vault name | - |

**Important**: You must set `OBSIDIAN_VAULT` to your vault's name for the workflow to open tasks in Obsidian. To find your vault name, open Obsidian and look at the vault switcher (bottom-left), or check the folder name of your vault.

For more reliability (especially if you rename your vault), also set `OBSIDIAN_VAULT_ID`:
1. Open Obsidian
2. Open the Developer Console (`⌘⌥I` or `Ctrl+Shift+I`)
3. Run: `app.appId`
4. Copy the returned ID and set it as `OBSIDIAN_VAULT_ID`

### Optional

| Variable | Description | Default |
|----------|-------------|---------|
| `OBSIDIAN_VAULT_ID` | Stable vault ID (preferred over name) | - |
| `OBSIDIAN_VAULT_PATH` | Explicit path to vault folder | - |
| `TASKNOTES_API_BASE` | TaskNotes API endpoint | `http://localhost:8080/api` |
| `TASKNOTES_TOKEN` | Bearer token for API auth | - |

### Tuning

| Variable | Description | Default |
|----------|-------------|---------|
| `TASK_FETCH_LIMIT` | Max tasks to fetch from API | `400` |
| `TASK_RETURN_LIMIT` | Max tasks shown in Alfred | `50` |
| `TASK_SUBTITLE_FIELDS` | Fields shown in subtitle | `due,scheduled,projects` |
| `TASK_CACHE_TTL_SECONDS` | Cache freshness duration | `5` |
| `TASK_CACHE_MAX_STALE_SECONDS` | Max cache age before refresh | `600` |
| `TASK_CACHE_RERUN_SECONDS` | Delay before background refresh on rerun | `0.4` |
| `TASK_CACHE_REFRESH_BACKOFF_SECONDS` | Min time between background refreshes | `5` |
| `TIME_ACTIVE_CACHE_TTL_SECONDS` | Active time tracking cache TTL | `1` |
| `TASK_DETAIL_CACHE_TTL_SECONDS` | Task detail cache TTL | `2` |

### Behavior

| Variable | Description | Default |
|----------|-------------|---------|
| `AUTO_START_OBSIDIAN_FOR_API` | Auto-launch Obsidian if API unreachable | `1` |
| `LAUNCH_OBSIDIAN_ON_ERROR` | Launch Obsidian on connection errors | `1` |
| `TASKNOTES_STARTUP_WAIT_SECONDS` | Max wait time for Obsidian to start | `12` |
| `TASKNOTES_HEALTH_POLL_INTERVAL_SECONDS` | Health check polling interval | `0.25` |
| `TASKNOTES_BOOTSTRAP_NOTIFY` | Show notification during Obsidian startup | `1` |

## Time Tracking

TaskNotes enforces a single active time tracking session. When you start tracking a new task:

1. Any currently tracked task is automatically stopped
2. The new task begins tracking
3. The tracked task appears at the top of your list with a ⏱ icon

Toggle tracking with `⌃↩` - if the task is already being tracked, it stops; otherwise, it starts.

## Subtitle Display

Task subtitles show relevant metadata with smart formatting:

- **Relative dates**: "Today", "Tomorrow", "Wed", "Feb 15"
- **Overdue indicator**: "3d ago" for past due dates
- **Projects**: Displayed without `[[brackets]]`
- **Tracking status**: Shows elapsed time when active

Example subtitle: `Due: Today • Scheduled: Tomorrow • Projects: Work, Personal`

## Troubleshooting

### "TaskNotes API isn't ready yet"

1. Ensure Obsidian is running
2. Verify TaskNotes HTTP API is enabled in settings
3. Check the API port matches your configuration
4. Try `curl http://localhost:8080/api/health` in terminal

### Tasks not appearing

1. Check `TASK_FETCH_LIMIT` isn't too low
2. Verify tasks aren't completed or archived
3. Clear cache: delete files in `~/Library/Caches/com.runningwithcrayons.Alfred/Workflow Data/`

### Authentication errors

1. Verify `TASKNOTES_TOKEN` matches the token in TaskNotes settings
2. Ensure no trailing spaces in the token value

### Obsidian not opening tasks

1. Set either `OBSIDIAN_VAULT` or `OBSIDIAN_VAULT_ID` in workflow variables
2. `OBSIDIAN_VAULT_ID` is more reliable if you rename your vault

## Architecture

```
Alfred Trigger (⌥J / "tn")
       ↓
TaskNotes Script Filter
       ↓
   [JSON Output → Alfred UI]
       ↓
   User selects item
       ↓
Conditional (JSON check)
   ├── JSON Action (modifier keys) → Run Script → TaskNotes API
   └── Task Path (Enter key) → Task Actions Menu → Run Script → TaskNotes API
```

### Core Modules

| File | Purpose |
|------|---------|
| `list_or_parse_task.py` | Main script filter - search, filter, display tasks |
| `task_actions.py` | Task Actions menu - display action options for a task |
| `open_or_create_task.py` | Action handler - create, open, track, delete, archive tasks |
| `nlp_task_create.py` | Natural language parser for task creation |
| `tasknotes_alfred.py` | TaskNotes API client |
| `cache.py` | Caching layer with TTL and stale-while-revalidate |

## License

MIT License

## Credits

- [TaskNotes](https://tasknotes.dev) - The excellent Obsidian plugin this workflow integrates with
- [Alfred](https://www.alfredapp.com/) - The productivity app for macOS
