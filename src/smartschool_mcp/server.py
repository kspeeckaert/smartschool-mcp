"""
Read-only MCP server for Smartschool.

Version: 1.0.20261018

Uses the unofficial `smartschool` library (https://github.com/svaningelgem/smartschool)
and exposes three tools:

* get_unread_messages: the unread messages in the inbox (headers only)
* get_tasks: the open tasks (GR, KL, HU, ME) after the current time
* get_lessons: the timetable for one day, from the Smartschool planner

The login comes from the environment variables SMARTSCHOOL_USERNAME, SMARTSCHOOL_PASSWORD,
SMARTSCHOOL_MAIN_URL and SMARTSCHOOL_MFA (the library's EnvCredentials).
"""

import argparse
import sys
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from logprise import logger  # the loguru logger that the smartschool library also uses
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from smartschool import (
    EnvCredentials,
    MessageHeaders,
    PlannedElement,
    PlannedElements,
    Smartschool,
    SmartSchoolAuthenticationError,
    SmartSchoolException,
)

from smartschool_mcp import __version__  # absolute import: `mcp dev` loads this file by path

TZ = ZoneInfo("Europe/Brussels")

# The assignment types that the school uses, in the order of importance.
TaskType = Literal["GR", "KL", "HU", "ME"]
TASK_ORDER: tuple[str, ...] = ("GR", "KL", "HU", "ME")

# Planner element types that make up the timetable in the web app's day and week view.
# planned-placeholders are timetable slots without lesson content from the teacher.
TIMETABLE_TYPES = ("planned-lessons", "planned-placeholders", "planned-school-activities")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer(
    "smartschool",
    version=__version__,
    instructions=(
        "Read-only access to one student's Smartschool account: unread messages, open tasks and lessons. "
        "Task types: GR = Grote toets (big test), KL = Kleine toets (small test), HU = Huistaak (homework), "
        "ME = Meebrengen (bring along). Course names, topics and messages are in Dutch."
    ),
    log_level="WARNING",
)


# --------------------------------------------------------------------------------------------------
# Output models
# --------------------------------------------------------------------------------------------------


class UnreadMessage(BaseModel):
    id: int = Field(description="Smartschool message id.")
    sender: str
    subject: str
    received: datetime
    has_attachment: bool


class UnreadMessages(BaseModel):
    count: int
    messages: list[UnreadMessage]


class Task(BaseModel):
    day: date
    start: datetime
    type: str = Field(description="GR, KL, HU or ME. A to-do without an assignment type has its planner type here.")
    type_name: str = Field(description="Name of the type in the web app, for example 'Grote toets'.")
    course: str
    title: str


class Tasks(BaseModel):
    count: int
    tasks: list[Task]


class LessonTask(BaseModel):
    type: str
    title: str


class Lesson(BaseModel):
    start: str = Field(description="Start time, HH:MM.")
    end: str = Field(description="End time, HH:MM.")
    course: str
    topics: list[str] = Field(description="Lesson topics from the teacher. Empty if the teacher added none.")
    tasks: list[LessonTask] = Field(description="Open tasks for this course that start in this slot.")


class LessonDay(BaseModel):
    day: date
    weekday: str
    lessons: list[Lesson]


# --------------------------------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------------------------------

_session: Smartschool | None = None
_lock = threading.Lock()  # MCPServer runs plain `def` tools in threads; the requests session is shared.


@contextmanager
def open_session() -> Iterator[Smartschool]:
    """Yield the shared, logged-in session and turn library errors into tool errors."""
    global _session
    with _lock:
        try:
            if _session is None:
                creds = EnvCredentials()
                creds.validate()
                _session = Smartschool(creds)
            yield _session
        except RuntimeError as exc:  # raised by Credentials.validate() for missing values
            raise ToolError(f"Credentials incomplete. Set the SMARTSCHOOL_* environment variables. {exc}") from exc
        except SmartSchoolAuthenticationError as exc:
            _session = None
            raise ToolError(f"Login failed: {exc.message}") from exc
        except SmartSchoolException as exc:
            raise ToolError(f"Smartschool error: {exc.message}") from exc


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------


def _courses(element: PlannedElement) -> str:
    return ", ".join(course.name for course in element.courses)


def _badge(element: PlannedElement) -> str:
    """Return the assignment type abbreviation (for example GR), or the planner type for a task without one."""
    return element.assignment_type.abbreviation if element.assignment_type else element.planned_element_type


def _sort_key(element: PlannedElement) -> tuple:
    """Sort by day, then GR, KL, HU, ME, then other types, then by time."""
    code = _badge(element)
    rank = TASK_ORDER.index(code) if code in TASK_ORDER else len(TASK_ORDER)
    start = element.period.date_time_from
    return (start.date(), rank, code, start)


def _planned_tasks(session: Smartschool, first_day: date, days: int) -> list[PlannedElement]:
    """Return the assignments and to-dos from `first_day` 00:00 for `days` days, without the resolved ones."""
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=TZ)
    end = start + timedelta(days=days, seconds=-1)
    elements = list(PlannedElements(session, from_date=start, till_date=end))
    logger.debug("PlannedElements {} .. {}: {} items", start, end, len(elements))
    return [e for e in elements if e.resolved_status != "resolved"]


# --------------------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------------------


@mcp.tool(title="Unread Smartschool messages", annotations=READ_ONLY)
def get_unread_messages() -> UnreadMessages:
    """
    List the unread messages in the Smartschool inbox: sender, subject, date and attachment flag.

    Returns headers only. The message body is not fetched, because opening a message marks it as read.
    """
    with open_session() as session:
        headers = list(MessageHeaders(session))
    # The read flag is `status` (1 = read, 0 = unread). The `unread` field does not mean unread.
    unread = [h for h in headers if h.status == 0]
    logger.debug("MessageHeaders: {} messages, {} unread", len(headers), len(unread))
    messages = [
        UnreadMessage(id=h.id, sender=h.from_, subject=h.subject, received=h.date, has_attachment=bool(h.attachment))
        for h in sorted(unread, key=lambda h: h.date, reverse=True)
    ]
    return UnreadMessages(count=len(messages), messages=messages)


@mcp.tool(title="Open Smartschool tasks", annotations=READ_ONLY)
def get_tasks(
    days: Annotated[int, Field(ge=1, le=35, description="Number of days to look ahead, from today.")] = 35,
    types: Annotated[
        list[TaskType] | None,
        Field(description="Only these task types. Leave empty for all types, including to-dos without a type."),
    ] = None,
) -> Tasks:
    """
    List the open tasks that start after the current time: tests, homework and things to bring.

    Types: GR = Grote toets, KL = Kleine toets, HU = Huistaak, ME = Meebrengen.
    Tasks marked as done are left out. Sorted by day, then GR, KL, HU, ME, then time.
    """
    now = datetime.now(tz=TZ)
    with open_session() as session:
        elements = _planned_tasks(session, now.date(), days)
    elements = [e for e in elements if e.period.date_time_from > now]
    if types:
        elements = [e for e in elements if _badge(e) in types]
    tasks = [
        Task(
            day=e.period.date_time_from.date(),
            start=e.period.date_time_from,
            type=_badge(e),
            type_name=e.assignment_type.name if e.assignment_type else "",
            course=_courses(e),
            title=e.name,
        )
        for e in sorted(elements, key=_sort_key)
    ]
    return Tasks(count=len(tasks), tasks=tasks)


@mcp.tool(title="Smartschool lessons for one day", annotations=READ_ONLY)
def get_lessons(
    day: Annotated[date | None, Field(description="The day (YYYY-MM-DD). Default: tomorrow.")] = None,
) -> LessonDay:
    """
    List the timetable for one day from the Smartschool planner, one entry per time slot.

    Each slot has the course, the lesson topics, and the open tasks (GR, KL, HU, ME) for that course in that slot.
    Two lessons of the same course in one slot are merged into one entry. Weekends and holidays have no lessons.
    """
    if day is None:
        day = datetime.now(tz=TZ).date() + timedelta(days=1)

    with open_session() as session:
        url = f"/planner/api/v1/planned-elements/user/{session.authenticated_user['id']}"
        params = {"from": day.isoformat(), "to": day.isoformat(), "types": ",".join(TIMETABLE_TYPES)}
        logger.debug("GET {} {}", url, params)
        timetable = [PlannedElement(**item) for item in session.json(url, data=params)]
        tasks = _planned_tasks(session, day, 1)

    timetable = [e for e in timetable if e.period.date_time_from.date() == day]

    # The web app shows two lessons of the same course in the same slot as one block ("2 lessen").
    slots: dict[tuple, list[PlannedElement]] = defaultdict(list)
    for element in timetable:
        key = (element.period.date_time_from, element.period.date_time_to, _courses(element) or element.name)
        slots[key].append(element)

    lessons = []
    for (start, end, title), elements in sorted(slots.items(), key=lambda item: (item[0][0], item[0][1])):
        course_ids = {course.id for e in elements for course in e.courses}
        slot_tasks = [
            LessonTask(type=_badge(t), title=t.name)
            for t in sorted(tasks, key=_sort_key)
            if t.period.date_time_from == start and course_ids & {course.id for course in t.courses}
        ]
        topics = list(dict.fromkeys(e.name for e in elements if e.name and e.name != title))
        lessons.append(Lesson(start=f"{start:%H:%M}", end=f"{end:%H:%M}", course=title, topics=topics, tasks=slot_tasks))

    return LessonDay(day=day, weekday=f"{day:%A}", lessons=lessons)


# --------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(prog="smartschool-mcp", description="Read-only MCP server for Smartschool (stdio).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Write debug output to stderr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    # stdout carries the MCP protocol: send all library logging to stderr.
    try:
        logger.remove(0)  # the default loguru stderr sink; keep the other handlers
    except ValueError:
        pass
    logger.add(sys.stderr, level="DEBUG" if args.verbose else "WARNING")

    mcp.run()


if __name__ == "__main__":
    main()
