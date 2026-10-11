from pathlib import Path

import pytest

from whyl import model
from whyl.adapters.codex import normalize
from whyl.blame import Line, apply_hunks, blame, reasons, replay, story, thinking
from whyl.model import Event, FileChange, NormalizedSession, Session, Thread, Turn
from whyl.present import highlights
from whyl.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "codex"
BREAK_FIX = FIXTURES / "20260929T172400Z-cb44c2"  # create calc.py, then break and fix it
SHOP = FIXTURES / "20261004T132159Z-39de3d"  # weak test noticed, strengthened, then the bug fixed


def texts(lines):
    return [line.text for line in lines]


def origins(lines):
    return [line.origin for line in lines]


def test_add_attributes_every_line():
    files = replay([("/f", "e1", "add", "a\nb\n", None)])
    assert texts(files["/f"]) == ["a", "b"]
    assert origins(files["/f"]) == ["e1", "e1"]


def test_update_replaces_and_keeps_history():
    files = replay([
        ("/f", "e1", "add", "a\nb\nc\n", None),
        ("/f", "e2", "update", "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n", None),
    ])
    assert texts(files["/f"]) == ["a", "B", "c"]
    assert origins(files["/f"]) == ["e1", "e2", "e1"]
    assert files["/f"][1].history == [("e1", "b"), ("e2", "B")]


def test_multi_hunk_update_shifts_later_hunks():
    lines = [Line(str(i), "e1") for i in range(1, 11)]
    # Insert two lines after 2, then replace line 8 (which is line 10 in the new file).
    apply_hunks(lines, "@@ -2,0 +3,2 @@\n+x\n+y\n@@ -8,1 +10,1 @@\n-8\n+EIGHT\n", "e2")
    assert texts(lines) == ["1", "2", "x", "y", "3", "4", "5", "6", "7", "EIGHT", "9", "10"]
    assert origins(lines)[2:4] == ["e2", "e2"] and origins(lines)[9] == "e2"


def test_pure_deletion():
    lines = [Line(t, "e1") for t in "abcd"]
    apply_hunks(lines, "@@ -2,2 +1,0 @@\n-b\n-c\n", "e2")
    assert texts(lines) == ["a", "d"]


def test_update_to_unrecorded_file_leaves_untouched_lines_unattributed():
    files = replay([("/f", "e1", "update", "@@ -3,2 +3,2 @@\n ctx\n-old\n+new\n", None)])
    lines = files["/f"]
    assert texts(lines) == [None, None, "ctx", "new"]
    assert origins(lines) == [None, None, None, "e1"]


def test_delete_and_move():
    files = replay([
        ("/a", "e1", "add", "x\n", None),
        ("/b", "e1", "add", "y\n", None),
        ("/a", "e2", "delete", None, None),
        ("/b", "e3", "move", "@@ -1 +1 @@\n-y\n+z\n", "/c"),
    ])
    assert set(files) == {"/c"}
    assert texts(files["/c"]) == ["z"] and origins(files["/c"]) == ["e3"]


def test_hunk_without_line_numbers_is_reported(tmp_path):
    warnings = []
    replay([("/f", "e1", "update", "@@\n-a\n+b\n", None)], warnings)
    assert warnings and "e1" in warnings[0]


def test_blame_break_fix_fixture(tmp_path):
    store = Store(tmp_path / "h.db")
    store.ingest(normalize(BREAK_FIX))
    result = blame(store, "/workspace/calc.py")
    assert not result.on_disk
    assert [l.text for l in result.lines] == ["def add(a, b):", "    return a + b"]

    line = result.lines[1]
    assert line.origin.kind == model.FILE_CHANGE
    turn = store.turn(line.origin.turn_id)
    assert turn["seq"] == 2
    assert [text for _, text in line.history] == ["    return a + b", "    return a - b", "    return a + b"]

    assert line.rewrites == 2

    with pytest.raises(LookupError):
        blame(store, "/workspace/nope.py")


def _session_writing(path: Path, content: str) -> NormalizedSession:
    session = Session("s1", "test-agent", None, str(path.parent), 0, 1, "completed", "test")
    event = Event("s1:1", "s1", "t", "u", 1, model.FILE_CHANGE, "completed", 0, 1,
                  file_changes=[FileChange(str(path), model.ADD, content)])
    return NormalizedSession(session, [Thread("t", "s1")], [Turn("u", "t", 1)], [event])


def test_blame_aligns_with_disk_after_human_edits(tmp_path):
    target = tmp_path / "f.py"
    store = Store(tmp_path / "h.db")
    store.ingest(_session_writing(target, "one\ntwo\nthree\n"))
    # A human inserts a line and edits another after the agent finished.
    target.write_text("zero\none\nTWO\nthree\n")
    result = blame(store, str(target))
    assert result.on_disk
    assert [(l.text, l.origin.id if l.origin else None) for l in result.lines] == [
        ("zero", None), ("one", "s1:1"), ("TWO", None), ("three", "s1:1")]


def test_declined_file_changes_are_not_blamed(tmp_path):
    target = tmp_path / "f.py"
    normalized = _session_writing(target, "x\n")
    normalized.events[0].status = "declined"
    store = Store(tmp_path / "h.db")
    store.ingest(normalized)
    with pytest.raises(LookupError):
        blame(store, str(target))


def test_story_tells_the_whole_turn(tmp_path):
    store = Store(tmp_path / "h.db")
    store.ingest(normalize(SHOP))
    line = blame(store, "/workspace/shop.py").lines[5]
    assert line.text == "    return price * (1 - percent / 100)"

    told = story(store, line.origin)
    assert told.prompt.startswith("apply_discount in shop.py is buggy")
    steps = [(e.kind, e.status) for e in told.steps]
    origin_at = told.steps.index(line.origin)
    # Before the fix: the test file is written, tests fail, the weak test is strengthened, tests fail again.
    before = told.steps[:origin_at]
    assert [c.path.rsplit("/", 1)[-1] for e in before if e.kind == model.FILE_CHANGE
            for c in e.file_changes] == ["test_shop.py", "test_shop.py"]
    assert any("accidentally passed" in e.payload.get("text", "") for e in before)
    assert [e.payload["exit_code"] for e in before if e.kind == model.COMMAND][-3:] == [127, 1, 1]
    # After: the passing test run, then the final answer, and nothing from turn 2.
    after = told.steps[origin_at + 1:]
    assert [(e.kind, e.status) for e in after] == [(model.COMMAND, "completed"), (model.AGENT_MESSAGE, "completed")]
    assert after[-1].payload["phase"] == "final"
    assert all(e.turn_id == line.origin.turn_id for e in told.steps)
    assert (model.TOKEN_USAGE, None) not in steps
    # Why: what the agent said since the file last changed, and the failing run just before.
    assert [(r.kind, r.event.seq) for r in told.reasons] == [
        ("said", next(e.seq for e in reversed(before) if e.kind == model.AGENT_MESSAGE)),
        ("failing", [e for e in before if e.kind == model.COMMAND][-1].seq)]
    assert told.check is after[0] and told.final is after[-1]


def test_reasons_come_from_since_the_file_last_changed():
    def event(seq, kind, payload, status="completed", changes=()):
        return model.Event(f"e{seq}", "s", "t", "u", seq, kind, status, seq * 1000, seq * 1000, payload,
                           file_changes=[model.FileChange(path, model.UPDATE, "") for path in changes])
    failing = {"command": "python3 -m pytest", "exit_code": 1, "output": "1 failed"}
    steps = [
        event(1, model.AGENT_MESSAGE, {"text": "First I'll look around."}),
        event(2, model.FILE_CHANGE, {}, changes=["/w/a.py"]),
        event(3, model.REASONING, {"summary": [], "content": ["The test fails because of rounding."]}),
        event(4, model.AGENT_MESSAGE, {"text": "Fixing the rounding."}),
        event(5, model.COMMAND, failing, status="failed"),
        event(6, model.FILE_CHANGE, {}, changes=["/w/b.py"]),  # another file: doesn't reset the window
        event(7, model.COMMAND, {"command": "sed -i s/x/y/ a.py", "description": "Round half up", "exit_code": 0},
              changes=["/w/a.py"]),
    ]
    told = reasons(steps, steps[-1])
    assert [(r.kind, r.event.seq) for r in told] == [("note", 7), ("thought", 3), ("said", 4), ("failing", 5)]
    assert thinking(steps[2]) == "The test fails because of rounding."
    # A passing run isn't a reason, and nothing from before the file's previous change counts.
    steps[4] = event(5, model.COMMAND, {**failing, "exit_code": 0, "output": "1 passed"})
    steps[6].payload.pop("description")
    assert [r.kind for r in reasons(steps, steps[-1])] == ["thought", "said"]
    assert reasons(steps[:3] + [steps[-1]], steps[-1])[0].event.seq == 3


def test_prompt_words_that_name_the_change():
    code = ["def multiply_arrays(a, b):", "    if len(a) != len(b):"]
    assert highlights("Add an array multiplication at the end of shop.py", code, "/w/shop.py") == \
        ["array", "multiplication", "shop.py"]
    assert highlights("the checkout total is wrong, fix the bug", ["def applyDiscount(price):"], "/w/x.py") == []
    assert highlights("make applyDiscount round", ["def applyDiscount(price):"], "/w/x.py") == ["applyDiscount"]
