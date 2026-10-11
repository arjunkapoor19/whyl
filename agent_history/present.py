"""What an event or a line's author amounts to, independent of how it is shown.

The terminal (`cli.py`) and the viewer (`view.py`) both render these, so they always
agree on what a step is: a test or a command, a read or a tool call, and how it went.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field

from agent_history import model
from agent_history.blame import Author, diff_stats, thinking
from agent_history.commands import is_test, read_only_label, summarize, verdict
from agent_history.model import Event
from agent_history.store import Store

SIGNS = {model.ADD: "+", model.DELETE: "-", model.UPDATE: "~", model.MOVE: "→"}


@dataclass
class Outline:
    """One step, classified. `label` is what the step is (user, agent, answer, read, test,
    command, edit, approval, tool, subagent, error, thinking); `title` is its full text."""
    label: str
    title: str
    status: str | None = None
    summary: str | None = None  # e.g. a test verdict, or the prompt handed to a sub-agent
    note: str | None = None  # the agent's own one-line description of the step, when it gave one


def outline(event: Event, cwd: str | None = None) -> Outline | None:
    """The step an event is, or None if it isn't worth a line in a timeline."""
    p = event.payload
    if event.kind == model.USER_MESSAGE:
        return Outline("user", p.get("text") or "")
    if event.kind == model.AGENT_MESSAGE:
        return Outline("answer" if p.get("phase") == "final" else "agent", p.get("text") or "")
    if event.kind == model.COMMAND:
        reads = read_only_label(p) if event.status == "completed" else None
        if reads:
            return Outline("read", reads, event.status)
        summary = summarize(p, event.status)
        if summary is None and p.get("exit_code") not in (0, None):
            summary = f"exit {p['exit_code']}"
        return Outline("test" if is_test(p.get("command")) else "command", p.get("command") or "", event.status,
                       summary, p.get("description"))
    if event.kind == model.FILE_CHANGE:
        files = ", ".join(f"{SIGNS.get(c.kind, '~')}{relative(c.path, cwd)}" for c in event.file_changes)
        return Outline("edit", files, event.status)
    if event.kind == model.APPROVAL:
        return Outline("approval", p.get("command") or p.get("subject") or "", event.status)
    if event.kind == model.TOOL_CALL:
        reads = read_only_label(p) if event.status == "completed" else None
        if reads:
            return Outline("read", reads, event.status)
        return Outline("tool", "/".join(str(x) for x in (p.get("server"), p.get("tool")) if x), event.status)
    if event.kind == model.SUBAGENT_CALL:
        receivers = ", ".join(t[-8:] for t in p.get("receiver_thread_ids") or [])
        return Outline("subagent", f"{p.get('action')}{' → ' + receivers if receivers else ''}", event.status,
                       p.get("description") or p.get("prompt"))
    if event.kind == model.ERROR:
        return Outline("error", p.get("message") or "", event.status)
    if event.kind == model.REASONING and thinking(event):
        return Outline("thinking", thinking(event))
    return None


@dataclass
class Work:
    """A turn at a glance: how long it took, how many steps, what it changed, how its tests went."""
    took: int | None  # milliseconds
    steps: int
    files: list[tuple[str, int, int]] = field(default_factory=list)  # (path, lines added, lines removed)
    tests: list[str] = field(default_factory=list)  # "passed" / "failed", in order


def work(turn: sqlite3.Row | None, steps: list[Event]) -> Work:
    files: dict[str, list[int]] = {}
    for event in steps:
        for change in event.file_changes:
            added, removed = diff_stats(change)
            total = files.setdefault(change.path, [0, 0])
            total[0] += added
            total[1] += removed
    tests = [v for v in (verdict(e.payload, e.status) for e in steps if e.kind == model.COMMAND) if v]
    times = [t for e in steps for t in (e.started_at, e.ended_at) if t]
    start = (turn["started_at"] if turn else None) or (min(times) if times else None)
    end = max(times + ([turn["ended_at"]] if turn and turn["ended_at"] else []), default=None)
    return Work(end - start if start and end else None, sum(1 for e in steps if outline(e) is not None),
                [(path, a, r) for path, (a, r) in files.items()], tests)


@dataclass
class Brief:
    """Another turn of the same session, in one line."""
    id: str
    seq: int
    prompt: str | None
    status: str | None
    started_at: int | None
    ended_at: int | None
    steps: int
    files: list[str]


def turns_around(store: Store, turn: sqlite3.Row) -> tuple[list[Brief], list[Brief]]:
    """The other turns of the thread `turn` is in: those before it and those after, in order."""
    turns = [t for t in store.turns(turn["session_id"]) if t["thread_id"] == turn["thread_id"]]
    events: dict[str, list[Event]] = {}
    for event in store.events(session_id=turn["session_id"]):
        events.setdefault(event.turn_id or "", []).append(event)
    briefs = []
    for t in turns:
        mine = events.get(t["id"], [])
        files = list(dict.fromkeys(c.path for e in mine for c in e.file_changes))
        briefs.append(Brief(t["id"], t["seq"], t["input_text"], t["status"], t["started_at"], t["ended_at"],
                            sum(1 for e in mine if e.kind != model.USER_MESSAGE and outline(e) is not None), files))
    at = next((i for i, t in enumerate(turns) if t["id"] == turn["id"]), 0)
    return briefs[:at], briefs[at + 1:]


WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9]+)*")
PARTS = re.compile(r"_+|\.|(?<=[a-z0-9])(?=[A-Z])")
COMMON = {"the", "and", "for", "with", "that", "this", "from", "into", "then", "than", "them", "they", "you",
          "your", "are", "was", "were", "not", "but", "all", "any", "can", "add", "use", "make", "should", "would",
          "could", "have", "has", "its", "def", "return", "self", "none", "true", "false", "class", "import",
          "print", "const", "function", "let", "var", "new", "end", "out", "run", "file", "line", "code", "when"}


def highlights(prompt: str | None, code: list[str], path: str) -> list[str]:
    """Words of the prompt that name what a change touched: identifiers in its code (or a close
    form, like "array" for `multiply_arrays`) and the file's name. Matching only, no judgement."""
    if not prompt:
        return []
    names, parts = {os.path.basename(path).lower()}, set()
    for word in WORD.findall("\n".join(code)):
        names.add(word.lower())
        parts.update(part.lower() for part in PARTS.split(word) if len(part) >= 4)
    found: list[str] = []
    for word in WORD.findall(prompt):
        low = word.lower()
        if len(low) < 3 or low in COMMON or word in found:
            continue
        if low in names or (len(low) >= 4 and any(_close(low, part) for part in parts)):
            found.append(word)
    return found


def _close(a: str, b: str) -> bool:
    """Same word in another form: they share their first five letters (or all of the shorter one's)."""
    shared = len(os.path.commonprefix([a, b]))
    return shared >= min(5, len(a), len(b))


def author_name(author: Author | None) -> str:
    """The agent's name up to its first `-`, `you` for edits outside any agent, `·` for pre-existing lines."""
    if author is None:
        return ""
    if author.kind == "outside":
        return "you"
    if author.kind == "baseline":
        return "·"
    return (author.agent or "agent").split("-")[0]


def shorten(command: str, cwd: str | None) -> str:
    """A command with the session's folder written as `.`, so what it did stays readable."""
    if not cwd:
        return command
    return command.replace(cwd.rstrip("/") + "/", "").replace(cwd.rstrip("/"), ".")


def prompt_text(text: str | None) -> str | None:
    """A prompt as the person typed it: without the quote bar some agents prefix it with."""
    return text.lstrip("▎").strip() if text else text


def relative(path: str, cwd: str | None) -> str:
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return os.path.relpath(path, cwd)
    return path
