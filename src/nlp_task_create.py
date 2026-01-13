#!/usr/bin/env python3
"""Lightweight NLP parsing for TaskNotes task creation.

Design goals:
  - Fast (runs on every keystroke in Alfred)
  - Conservative defaults for *bare* dates (assume future, like Todoist)
  - Explicit keywords (due/do/sch/by) should do what you said—even if it's in the past

Supported:
  Scheduled dates (default for *bare* dates):
    - today | tod
    - tomorrow | tmr | tom
    - weekdays: mon/tue/wed/thu/fri/sat/sun (incl. today)
    - next <weekday>
    - ISO date: YYYY-MM-DD
    - US date: M/D or M/D/YYYY
    - Month name: jan 2 [2026]

  Past-relative (explicitly past):
    - yesterday | yest
    - last <weekday>

  Explicit scheduled keyword:
    - do <date>
    - sch <date>
    - sch:<date>   (single-token date only, e.g. sch:2026-01-10)

  Explicit due keyword:
    - due <date>
    - due:<date>   (single-token date only)
    - by <date>

  Priority:
    - p1 | p2 | p3  (mapped to High/Medium/Low for TaskNotes)

  Tags:
    - #tag

  Projects:
    - +Project   (multi-token supported; e.g. +2025 Wardrobe Upgrade)

  Details / body:
    - Title + metadata on the left, details on the right:
        Title here +Project due tomorrow // line 1  line 2
      (Everything after `//` becomes details. Use either double-space or literal "\\n" for newlines.)

New (strict, not keyword-gated):
  Relative offsets (scheduled by default unless preceded by due/by):
    - in <N> <unit>               e.g. "in two weeks", "in 3 months", "in a year"
    - after <N> <unit>            e.g. "after 14 days"
    - <N> <unit> from today|now   e.g. "two weeks from today"
    Units: day(s), week(s), month(s), year(s)
    N: digits or one..twelve or a/an (as 1)

  Nth weekday of month (scheduled by default unless preceded by due/by):
    - <ordinal> <weekday> of <month> [year]
      e.g. "1st monday of jan", "first monday of january", "last friday of nov 2026"
    Ordinal: 1st..5th, first..fifth, last

Notes:
  - Contexts are intentionally NOT parsed (user disabled in TaskNotes).
  - We ignore @token with a warning to avoid silently eating title text.

References:
  - We use calendar.monthrange() to get days-in-month for safe month/year math. :contentReference[oaicite:2]{index=2}
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional, Tuple


_WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# Ordinals for nth weekday parsing
_ORDINALS = {
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "last": -1,
}

# Word numbers for relative offsets (bounded for simplicity)
_WORD_NUMS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_RE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_RE_US = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")
_RE_DAY = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[,]?$")


@dataclass(frozen=True)
class ParsedCreate:
    raw: str
    title: str
    # Everything after the `//` delimiter, normalized into real newlines.
    details: Optional[str] = None
    scheduled: Optional[str] = None  # ISO
    due: Optional[str] = None        # ISO
    priority: Optional[str] = None   # High | Medium | Low (or your custom values)
    tags: Tuple[str, ...] = ()
    projects: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


def _iso(d: date) -> str:
    return d.isoformat()


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except Exception:
        return None


def _days_in_month(y: int, m: int) -> int:
    # calendar.monthrange returns (weekday_of_first_day, days_in_month). :contentReference[oaicite:3]{index=3}
    return calendar.monthrange(y, m)[1]


def _add_months(base: date, months: int) -> date:
    """Add months to a date, clamping day to the target month's length."""
    total = (base.year * 12 + (base.month - 1)) + months
    ny = total // 12
    nm = total % 12 + 1
    nd = min(base.day, _days_in_month(ny, nm))
    return date(ny, nm, nd)


def _add_years(base: date, years: int) -> date:
    """Add years to a date, clamping day for Feb 29 etc."""
    ny = base.year + years
    nm = base.month
    nd = min(base.day, _days_in_month(ny, nm))
    return date(ny, nm, nd)


def _next_weekday(today: date, target_weekday: int, *, force_next_week: bool = False) -> date:
    """Return the next occurrence of target weekday.
    If force_next_week=True and today is the target, return 7 days ahead.
    Otherwise include today.
    """
    delta = (target_weekday - today.weekday()) % 7
    if force_next_week and delta == 0:
        delta = 7
    return today + timedelta(days=delta)


def _prev_weekday(today: date, target_weekday: int) -> date:
    """Return the previous occurrence of target weekday (always in the past)."""
    delta = (today.weekday() - target_weekday) % 7
    if delta == 0:
        delta = 7
    return today - timedelta(days=delta)


def _clean_tok(s: str) -> str:
    # Light punctuation stripping for phrase matching (keeps digits/letters intact).
    return (s or "").strip().strip(",.;")


def _parse_int_or_wordnum(tok: str) -> Optional[int]:
    t = _clean_tok(tok).casefold()
    if t.isdigit():
        try:
            return int(t)
        except Exception:
            return None
    return _WORD_NUMS.get(t)


def _parse_unit(tok: str) -> Optional[str]:
    t = _clean_tok(tok).casefold()
    if t in ("day", "days"):
        return "days"
    if t in ("week", "weeks"):
        return "weeks"
    if t in ("month", "months"):
        return "months"
    if t in ("year", "years"):
        return "years"
    return None


def _apply_relative_offset(today: date, n: int, unit: str) -> date:
    if unit == "days":
        return today + timedelta(days=n)
    if unit == "weeks":
        return today + timedelta(days=7 * n)
    if unit == "months":
        return _add_months(today, n)
    if unit == "years":
        return _add_years(today, n)
    return today


def _nth_weekday_of_month(y: int, m: int, weekday: int, ordinal: int) -> Optional[date]:
    """Compute the nth weekday of a given month/year.
    ordinal: 1..5 or -1 for last.
    weekday: 0=Mon..6=Sun.
    """
    first_wd, dim = calendar.monthrange(y, m)
    if ordinal == -1:
        last_wd = (first_wd + (dim - 1)) % 7
        delta = (last_wd - weekday) % 7
        day = dim - delta
        return date(y, m, day)

    if ordinal < 1:
        return None

    delta = (weekday - first_wd) % 7
    day = 1 + delta + (ordinal - 1) * 7
    if day > dim:
        return None
    return date(y, m, day)


def _parse_relative_phrase(tokens: List[str], i: int, today: date) -> Tuple[Optional[str], int]:
    """Parse strict relative phrases at tokens[i].

    Supported:
      - in <N> <unit>
      - after <N> <unit>
      - <N> <unit> from today|now
    """
    if i >= len(tokens):
        return None, 0

    t0 = _clean_tok(tokens[i]).casefold()

    # in/after <N> <unit>
    if t0 in ("in", "after") and i + 2 < len(tokens):
        n = _parse_int_or_wordnum(tokens[i + 1])
        unit = _parse_unit(tokens[i + 2])
        if n is not None and unit:
            d = _apply_relative_offset(today, n, unit)
            return _iso(d), 3

    # <N> <unit> from today|now
    if i + 3 < len(tokens):
        n = _parse_int_or_wordnum(tokens[i])
        unit = _parse_unit(tokens[i + 1])
        t2 = _clean_tok(tokens[i + 2]).casefold()
        t3 = _clean_tok(tokens[i + 3]).casefold()
        if n is not None and unit and t2 == "from" and t3 in ("today", "now"):
            d = _apply_relative_offset(today, n, unit)
            return _iso(d), 4

    return None, 0


def _parse_nth_weekday_phrase(tokens: List[str], i: int, today: date, *, allow_past: bool) -> Tuple[Optional[str], int]:
    """Parse strict nth weekday phrases at tokens[i]:
       <ordinal> <weekday> of <month> [year]
    """
    if i + 3 >= len(tokens):
        return None, 0

    ord_tok = _clean_tok(tokens[i]).casefold()
    weekday_tok = _clean_tok(tokens[i + 1]).casefold()
    of_tok = _clean_tok(tokens[i + 2]).casefold()
    month_tok = _clean_tok(tokens[i + 3]).casefold()

    if ord_tok not in _ORDINALS:
        return None, 0
    if weekday_tok not in _WEEKDAYS:
        return None, 0
    if of_tok != "of":
        return None, 0
    if month_tok not in _MONTHS:
        return None, 0

    ordinal = _ORDINALS[ord_tok]
    weekday = _WEEKDAYS[weekday_tok]
    month = _MONTHS[month_tok]

    consumed = 4
    year: Optional[int] = None
    if i + 4 < len(tokens):
        ytok = _clean_tok(tokens[i + 4])
        if ytok.isdigit() and len(ytok) == 4:
            year = int(ytok)
            consumed = 5

    # Choose year if omitted: current year, and if it lands in the past (and allow_past=False), bump to next year.
    y = year if year is not None else today.year
    d = _nth_weekday_of_month(y, month, weekday, ordinal)
    if d is None:
        return None, 0

    if year is None and (d < today) and (not allow_past):
        d2 = _nth_weekday_of_month(today.year + 1, month, weekday, ordinal)
        if d2:
            d = d2

    return _iso(d), consumed


def _parse_date_phrase(tokens: List[str], i: int, today: date, *, allow_past: bool) -> Tuple[Optional[str], int]:
    """Parse a date starting at tokens[i].

    Returns (iso_date, consumed_tokens).

    allow_past:
      - True: accept past dates (for explicit keywords like due/do/sch/by)
      - False: for bare dates, roll forward when year omitted and date would be in the past
    """
    if i >= len(tokens):
        return None, 0

    # New: strict relative offsets (not keyword-gated)
    dt, consumed = _parse_relative_phrase(tokens, i, today)
    if dt and consumed:
        return dt, consumed

    # New: strict nth weekday of month (not keyword-gated)
    dt, consumed = _parse_nth_weekday_phrase(tokens, i, today, allow_past=allow_past)
    if dt and consumed:
        return dt, consumed

    t0 = tokens[i]
    low0 = _clean_tok(t0).casefold()

    # today / yesterday / tomorrow
    if low0 in ("today", "tod"):
        return _iso(today), 1
    if low0 in ("yesterday", "yest"):
        return _iso(today - timedelta(days=1)), 1
    if low0 in ("tomorrow", "tmr", "tom"):
        return _iso(today + timedelta(days=1)), 1

    # next <weekday>
    if low0 == "next" and i + 1 < len(tokens):
        low1 = _clean_tok(tokens[i + 1]).casefold()
        if low1 in _WEEKDAYS:
            d = _next_weekday(today, _WEEKDAYS[low1], force_next_week=True)
            return _iso(d), 2
        # next week / next month
        if low1 == "week":
            return _iso(today + timedelta(days=7)), 2
        if low1 == "month":
            return _iso(_add_months(today, 1)), 2

    # last <weekday>
    if low0 == "last" and i + 1 < len(tokens):
        low1 = _clean_tok(tokens[i + 1]).casefold()
        if low1 in _WEEKDAYS:
            d = _prev_weekday(today, _WEEKDAYS[low1])
            return _iso(d), 2

    # weekday (default to upcoming, including today)
    if low0 in _WEEKDAYS:
        d = _next_weekday(today, _WEEKDAYS[low0], force_next_week=False)
        return _iso(d), 1

    # ISO
    m = _RE_ISO.match(_clean_tok(t0))
    if m:
        y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
        d = _safe_date(y, mo, da)
        return (_iso(d), 1) if d else (None, 0)

    # US M/D[/Y]
    m = _RE_US.match(_clean_tok(t0))
    if m:
        mo, da = int(m.group(1)), int(m.group(2))
        y_raw = m.group(3)
        if y_raw:
            y = int(y_raw)
            if y < 100:
                y += 2000
        else:
            y = today.year

        d = _safe_date(y, mo, da)
        if d and (not y_raw) and (d < today) and (not allow_past):
            # Bare yearless date: assume *next* occurrence.
            d2 = _safe_date(today.year + 1, mo, da)
            if d2:
                d = d2
        return (_iso(d), 1) if d else (None, 0)

    # Month name: jan 2 [2026]
    if low0 in _MONTHS and i + 1 < len(tokens):
        mnum = _MONTHS[low0]
        day_tok = _clean_tok(tokens[i + 1])
        md = _RE_DAY.match(day_tok.casefold())
        if md:
            day_num = int(md.group(1))
            year = today.year
            consumed = 2
            explicit_year = False

            if i + 2 < len(tokens):
                ytok = _clean_tok(tokens[i + 2]).rstrip(",")
                if ytok.isdigit() and len(ytok) in (2, 4):
                    y = int(ytok)
                    if y < 100:
                        y += 2000
                    year = y
                    consumed = 3
                    explicit_year = True

            d = _safe_date(year, mnum, day_num)
            if d and (not explicit_year) and (d < today) and (not allow_past):
                # Bare month/day: assume *next* occurrence.
                d2 = _safe_date(today.year + 1, mnum, day_num)
                if d2:
                    d = d2
            return (_iso(d), consumed) if d else (None, 0)

    return None, 0


def parse_create_input(raw: str, *, today: Optional[date] = None) -> ParsedCreate:
    """Parse a raw quick-add string into title + metadata."""
    raw = (raw or "").strip()
    if today is None:
        today = date.today()

    if not raw:
        return ParsedCreate(raw="", title="")

    # Split input into:
    #   left: title + metadata (date parsing happens here)
    #   right: details/body (no metadata parsing here)
    left = raw
    details: Optional[str] = None
    if "//" in raw:
        before, after = raw.split("//", 1)
        left = before.strip()

        # Allow two ways to represent newlines from Alfred:
        #   - literal "\\n" sequences
        #   - double spaces ("  ")
        d = (after or "").strip()
        if d:
            d = re.sub(r" *\\n *", "\n", d)
            # Convert 2+ spaces into a single newline.
            d = re.sub(r" {2,}", "\n", d)
            details = d

    tokens = [t for t in left.split() if t]

    tags: List[str] = []
    projects: List[str] = []
    warnings: List[str] = []

    scheduled: Optional[str] = None
    due: Optional[str] = None
    priority: Optional[str] = None

    priority_map = {
            "p1": "High", "p2": "Medium", "p3": "Low",
            "!!!": "High", "!!": "Medium", "!": "Low",
        }

    keep: List[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = _clean_tok(tok).casefold()

        # Priority
        if low in priority_map:
            priority = priority_map[low]
            i += 1
            continue

        # Tags
        if tok.startswith("#") and len(tok) > 1:
            tag = re.sub(r"[\.,;:]+$", "", tok[1:].strip())
            if tag:
                tags.append(tag)
            i += 1
            continue

        # Projects
        #
        # Supports:
        #   +Work
        #   +2025 Wardrobe Upgrade
        #
        # We treat a project as "+<first token>" followed by any number of
        # non-metadata tokens, until the next recognized token type.
        if tok.startswith("+") and len(tok) > 1:
            first = re.sub(r"[\.,;:]+$", "", tok[1:].strip())
            if not first:
                i += 1
                continue

            proj_parts = [first]
            j = i + 1
            while j < len(tokens):
                nxt = tokens[j]
                low_nxt = _clean_tok(nxt).casefold()

                # Stop on other metadata tokens.
                if nxt.startswith(("#", "+", "@")):
                    break
                if low_nxt in ("due", "by", "do", "sch"):
                    break
                if low_nxt in priority_map:
                    break
                if low_nxt.startswith(("due:", "sch:")):
                    break

                # Stop if the next token starts a date phrase.
                dt, consumed = _parse_date_phrase(tokens, j, today, allow_past=True)
                if dt and consumed > 0:
                    break

                part = re.sub(r"[\.,;:]+$", "", nxt.strip())
                if part:
                    proj_parts.append(part)
                j += 1

            projects.append(" ".join(proj_parts).strip())
            i = j
            continue

        # Contexts disabled
        if tok.startswith("@") and len(tok) > 1:
            warnings.append(f"Ignored {tok} (contexts disabled)")
            i += 1
            continue

        # due:<date> (single-token date)
        if low.startswith("due:"):
            rest = tok[4:].strip()
            if rest:
                dt, _ = _parse_date_phrase([rest], 0, today, allow_past=True)
                if dt:
                    due = dt
                    i += 1
                    continue

        # due <date phrase>
        if low == "due":
            dt, consumed = _parse_date_phrase(tokens, i + 1, today, allow_past=True)
            if dt:
                due = dt
                i += 1 + consumed
                continue

        # by <date phrase> => due
        if low == "by":
            dt, consumed = _parse_date_phrase(tokens, i + 1, today, allow_past=True)
            if dt:
                due = dt
                i += 1 + consumed
                continue

        # sch:<date> (single-token date)
        if low.startswith("sch:"):
            rest = tok[4:].strip()
            if rest:
                dt, _ = _parse_date_phrase([rest], 0, today, allow_past=True)
                if dt:
                    scheduled = dt
                    i += 1
                    continue

        # do <date phrase> | sch <date phrase> | on <date phrase> | start <date phrase> | scheduled <date phrase>
        if low in ("do", "sch", "on", "start", "scheduled"):
            dt, consumed = _parse_date_phrase(tokens, i + 1, today, allow_past=True)
            if dt:
                scheduled = dt
                i += 1 + consumed
                continue

        # Bare date phrase => scheduled (future oriented)
        dt, consumed = _parse_date_phrase(tokens, i, today, allow_past=False)
        if dt and consumed > 0:
            scheduled = dt
            i += consumed
            continue

        keep.append(tok)
        i += 1

    title = " ".join(keep).strip()
    return ParsedCreate(
        raw=raw,
        title=title,
        details=details,
        scheduled=scheduled,
        due=due,
        priority=priority,
        tags=tuple(dict.fromkeys(tags)),
        projects=tuple(dict.fromkeys(projects)),
        warnings=tuple(warnings),
    )


def build_preview(parsed: ParsedCreate) -> str:
    pieces: List[str] = []
    if parsed.scheduled:
        pieces.append(f"Scheduled: {parsed.scheduled}")
    if parsed.due:
        pieces.append(f"Due: {parsed.due}")
    if parsed.priority:
        pieces.append(f"Priority: {parsed.priority}")
    if parsed.details:
        line_count = len([ln for ln in parsed.details.splitlines() if ln.strip()]) or 1
        pieces.append(f"Details: {line_count} line" + ("s" if line_count != 1 else ""))
    if parsed.tags:
        pieces.append("Tags: " + ", ".join(parsed.tags[:5]) + ("…" if len(parsed.tags) > 5 else ""))
    if parsed.projects:
        pieces.append("Projects: " + ", ".join(parsed.projects[:3]) + ("…" if len(parsed.projects) > 3 else ""))

    preview = " • ".join(pieces) if pieces else "No metadata detected"
    if parsed.warnings:
        preview = preview + "  ⚠️ " + " · ".join(parsed.warnings[:2])
        if len(parsed.warnings) > 2:
            preview += "…"
    return preview
