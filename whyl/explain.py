"""Plain-language explanations of turns, written by an agent CLI the person already has.

whyl calls no model API itself. It runs an installed agent's headless mode (each adapter says
how; they are registered in `sources.EXPLAINERS`) on a compact digest of what the record
shows about a turn, and keeps the answer, so a turn is explained once. A session is
explained by its own agent's CLI when that is installed, so its code and prompts go to no
provider that hadn't already seen them.

The agent runs in an empty folder next to the database, with no tools, nothing saved where
possible, and whyl's hooks switched off; sessions it leaves behind there are never synced.
Explanations are AI-generated text, not the record: they are always labelled with the CLI and
the model that wrote them.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from whyl import commands, hooks, model
from whyl.blame import diff_stats, observed_file_changes, thinking
from whyl.model import Event
from whyl.present import relative
from whyl.store import Store

TIMEOUT = 300  # seconds an explainer may take
BATCH = 30_000  # characters of digest sent in one call; more turns wait for the next
TURN_LIMIT = 6_000  # characters of digest per turn
DIFF_LINES = 20  # diff lines per edit in a digest
SETTLE = 10 * 60_000  # an unfinished turn is explained once it has been quiet this long

INSTRUCTIONS = """You explain what an AI coding agent did, for a developer reviewing its work.
Below are recorded turns: what the person asked, each step the agent took, and its final answer.

For each turn write 2 to 4 plain sentences, under 90 words in all: what the person wanted; what the agent changed and why,
in the agent's own stated reasons or what visibly prompted it (such as a failing test); how it
checked the result; and anything left unverified or worth a second look. State only what the record
shows; when a reason isn't recorded, say so rather than guess. No headings, no lists, no preamble.
Put code names in backticks.

Reply with only a JSON object mapping each turn's key to its explanation, like {"T1": "..."}."""

_lock = threading.Lock()  # one explanation run at a time: a second request for the same turns finds them saved


@dataclass(frozen=True)
class Answer:
    text: str
    model: str | None = None  # the model that actually answered, when the CLI says


def _plain(done: subprocess.CompletedProcess) -> Answer:
    return Answer(done.stdout.strip())


@dataclass(frozen=True)
class Explainer:
    """How to ask one agent CLI for an answer without opening it."""
    name: str  # the CLI's name, as `--with` takes it
    agent: str  # the agent whose sessions it explains first (its provider has seen them already)
    model: str | None  # the model asked for; None: the CLI's own default
    command: Callable[[str], "list[str]"]  # the command line that answers a prompt
    stdin: bool = False  # the prompt goes on stdin rather than in the command line
    answer: Callable[[subprocess.CompletedProcess], Answer] = _plain  # its finished run -> the answer

    @property
    def label(self) -> str:
        return self.label_for(self.model)

    def label_for(self, model: str | None) -> str:
        return f"{self.name} ({model})" if model else self.name

    def available(self) -> bool:
        return shutil.which(self.command("")[0]) is not None


class ExplainError(Exception):
    pass


def pick(explainers: list[Explainer], agent: str | None, wanted: str | None = None) -> Explainer | None:
    """The explainer to use: the one asked for (`--with`, or $WHYL_EXPLAIN_WITH), else the session's
    own agent's, else the first one installed."""
    wanted = wanted or os.environ.get("WHYL_EXPLAIN_WITH")
    if wanted:
        chosen = next((e for e in explainers if e.name == wanted), None)
        if chosen is None:
            raise ExplainError(f"unknown explainer '{wanted}' (known: {', '.join(e.name for e in explainers)})")
        return chosen if chosen.available() else None
    usable = [e for e in explainers if e.available()]
    return next((e for e in usable if e.agent == agent), usable[0] if usable else None)


def top_turns(store: Store, session_id: str) -> list[sqlite3.Row]:
    """The turns a person started; sub-agent turns are explained as part of the turn that delegated them."""
    return [t for t in store.turns(session_id) if store.spawner(t["thread_id"]) is None]


def explain(store: Store, explainers: list[Explainer], turns: list[sqlite3.Row], wanted: str | None = None,
            again: bool = False, now: int | None = None) -> dict[str, sqlite3.Row]:
    """Explain the turns that need it (never explained, or grown since and now settled; all of them
    with `again`), one call per session and batch, and return every saved explanation of `turns`."""
    now = now if now is not None else int(time.time() * 1000)
    ids = [t["id"] for t in turns]
    with _lock:
        saved = store.explanations(ids) if ids else {}
        by_session: dict[str, list[tuple[sqlite3.Row, str, str]]] = {}
        for turn in turns:
            events = _turn_events(store, turn)
            key = _key(turn, events)
            row = saved.get(turn["id"])
            settled = turn["status"] != "incomplete" or now - _last(turn, events) > SETTLE
            if again or (settled and (row is None or (row["key"] != key and row["text"] is not None))):
                session = store.find_session(turn["session_id"])
                by_session.setdefault(turn["session_id"], []).append(
                    (turn, key, digest(turn, events, session["cwd"] if session else None)))
        for session_id, todo in by_session.items():
            session = store.find_session(session_id)
            explainer = pick(explainers, session["agent"] if session else None, wanted)
            if explainer is None:
                raise ExplainError(f"no agent CLI found to write explanations ({', '.join(e.name for e in explainers)})")
            for batch in _batches(todo):
                _run_batch(store, explainer, batch, now)
        return store.explanations(ids) if ids else {}


def digest(turn: sqlite3.Row, events: list[Event], cwd: str | None) -> str:
    """What the record shows about a turn, compactly, for an explainer to read."""
    lines = [f"Status: {turn['status'] or 'unknown'}",
             f"The person asked: {_cut(turn['input_text'] or '(no prompt recorded)', 2000)}", "Steps:"]
    final = None
    for event in events:
        if event.kind == model.AGENT_MESSAGE and event.payload.get("phase") == "final":
            final = event
            continue
        lines += _step(event, cwd)
    if final is not None:
        lines.append(f"Final answer: {_cut(final.payload.get('text') or '', 2000)}")
    text = "\n".join(lines)
    if len(text) > TURN_LIMIT:  # keep the start and the end; the middle is counted
        head, tail = text[:TURN_LIMIT * 2 // 3], text[-TURN_LIMIT // 3:]
        text = f"{head}\n… ({len(text) - len(head) - len(tail)} characters of steps left out) …\n{tail}"
    return text


def _step(event: Event, cwd: str | None) -> list[str]:
    p = event.payload
    if event.kind == model.AGENT_MESSAGE:
        return [f"- said: {_cut(p.get('text') or '', 600)}"]
    if event.kind == model.REASONING:
        text = thinking(event)
        return [f"- thought: {_cut(text, 400)}"] if text else []
    if event.kind == model.COMMAND:
        line = f"- ran: {_cut(p.get('command') or '', 200)}"
        if p.get("description"):
            line += f" (agent's note: {_cut(p['description'], 200)})"
        summary = commands.summarize(p, event.status)
        exit_code = p.get("exit_code")
        line += f" -> {event.status or '?'}" + (f", exit {exit_code}" if exit_code not in (None, 0) else "")
        out = [line + (f" · {summary}" if summary else "")]
        return out + _diffs(event, cwd)
    if event.kind == model.FILE_CHANGE:
        return [f"- edited ({event.status or '?'}):"] + _diffs(event, cwd)
    if event.kind == model.TOOL_CALL:
        tool = "/".join(str(x) for x in (p.get("server"), p.get("tool")) if x) or "tool"
        return [f"- used {tool} -> {event.status or '?'}"]
    if event.kind == model.SUBAGENT_CALL:
        return [f"- delegated to a sub-agent: {_cut(p.get('description') or p.get('prompt') or '', 300)}"]
    if event.kind == model.ERROR:
        return [f"- error: {_cut(p.get('message') or '', 300)}"]
    if event.kind == model.APPROVAL:
        return [f"- asked approval for {_cut(p.get('command') or p.get('subject') or '', 150)} -> {event.status}"]
    return []


def _diffs(event: Event, cwd: str | None) -> list[str]:
    out = []
    for change in event.file_changes:
        added, removed = diff_stats(change)
        out.append(f"    {change.kind} {relative(change.path, cwd)} (+{added} -{removed})")
        body = (change.diff or "").splitlines()
        if change.kind == model.ADD:
            body = ["+" + line for line in body]
        out += ["      " + line[:160] for line in body[:DIFF_LINES]]
        if len(body) > DIFF_LINES:
            out.append(f"      … {len(body) - DIFF_LINES} more diff lines")
    return out


def _turn_events(store: Store, turn: sqlite3.Row) -> list[Event]:
    """A turn's events with the work of any sub-agent it delegated to, in place."""
    out = []
    for event in store.events(turn_id=turn["id"]):
        if event.kind in (model.TOKEN_USAGE, model.USER_MESSAGE):  # the prompt is the turn's input
            continue
        out.append(event)
        if event.kind == model.SUBAGENT_CALL:
            for thread in event.payload.get("receiver_thread_ids") or []:
                for child in store.db.execute("SELECT * FROM turns WHERE thread_id = ? ORDER BY seq", (thread,)):
                    out += _turn_events(store, child)
    for event in out:  # what whyl saw a step change replaces what the agent reported
        observation = store.observation_for(event)
        if observation is not None:
            event.file_changes = observed_file_changes(store, observation["id"])
    return out


def _key(turn: sqlite3.Row, events: list[Event]) -> str:
    return f"{turn['status']}:{len(events)}:{_last(turn, events)}"


def _last(turn: sqlite3.Row, events: list[Event]) -> int:
    times = [turn["started_at"] or 0, turn["ended_at"] or 0]
    times += [e.ended_at or e.started_at or 0 for e in events]
    return max(times)


def _batches(todo: list[tuple[sqlite3.Row, str, str]]) -> list[list[tuple[sqlite3.Row, str, str]]]:
    batches: list[list[tuple[sqlite3.Row, str, str]]] = [[]]
    size = 0
    for item in todo:
        if batches[-1] and size + len(item[2]) > BATCH:
            batches.append([])
            size = 0
        batches[-1].append(item)
        size += len(item[2])
    return batches


def _run_batch(store: Store, explainer: Explainer, batch: list[tuple[sqlite3.Row, str, str]], now: int) -> None:
    keys = {f"T{i}": item for i, item in enumerate(batch, 1)}
    prompt = INSTRUCTIONS + "\n\n" + "\n\n".join(f"=== {k} ===\n{item[2]}" for k, item in keys.items())
    by = explainer.label
    try:
        answer = ask(store, explainer, prompt)
        by = explainer.label_for(answer.model or explainer.model)
        answers = _parse(answer.text, list(keys))
        error = None
    except ExplainError as failure:
        answers, error = {}, str(failure)
    for k, (turn, key, _) in keys.items():
        text = answers.get(k)
        store.save_explanation(turn["id"], key, text, None if text else (error or "no explanation in the answer"),
                               by, now)


def ask(store: Store, explainer: Explainer, prompt: str) -> Answer:
    """Run the explainer on a prompt, in whyl's own folder, with whyl's hooks off."""
    store.explain_dir.mkdir(parents=True, exist_ok=True)
    done = quietly(explainer.command(prompt), store.explain_dir, prompt if explainer.stdin else "")
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()[-3:]
        raise ExplainError(f"{explainer.name} failed (exit {done.returncode}): {' '.join(detail)}")
    return explainer.answer(done)


def quietly(command: list[str], cwd=None, input: str = "", timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    """Run an agent CLI (in `cwd`) with whyl's hooks off, so whyl doesn't record its own explanations."""
    env = {**os.environ, hooks.OFF: "off", **({"PWD": str(cwd)} if cwd else {})}  # some CLIs read $PWD
    try:
        return subprocess.run(command, cwd=cwd, input=input, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as failure:
        raise ExplainError(f"{command[0]} didn't answer: {failure}") from failure


def _parse(text: str, keys: list[str]) -> dict[str, str]:
    start, end = text.find("{"), text.rfind("}")
    try:
        found = json.loads(text[start:end + 1]) if start != -1 and end > start else None
    except ValueError:
        found = None
    if isinstance(found, dict):
        return {k: str(v).strip() for k, v in found.items() if k in keys and str(v).strip()}
    if len(keys) == 1 and text.strip() and start == -1:  # a plain answer for a single turn
        return {keys[0]: text.strip()}
    raise ExplainError("the answer wasn't the JSON asked for")


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
