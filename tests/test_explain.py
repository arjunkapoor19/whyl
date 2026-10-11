"""`whyl explain`: turns explained by an agent CLI, once each, without whyl's hooks or history noticing."""

import http.client
import json
import os
import subprocess
import sys
import threading

import pytest

from whyl import explain, hooks, sources, view
from whyl.adapters import claude_code, codex, opencode
from whyl.cli import main
from whyl.explain import Explainer
from whyl.store import Store
from test_codex_rollout import SHOP_CAPTURE

# Stands in for an agent CLI: answers every turn key in the prompt, and notes each call (where it ran,
# whether whyl's hooks were off) in calls.jsonl next to it.
FAKE = """
import json, os, re, sys
prompt = sys.stdin.read()
with open(sys.argv[1], "a") as calls:
    calls.write(json.dumps({"cwd": os.getcwd(), "pwd": os.environ.get("PWD"), "hooks": os.environ.get("WHYL_HOOKS"),
                            "keys": re.findall(r"=== (T\\d+) ===", prompt), "prompt": prompt}) + "\\n")
if os.environ.get("FAKE_FAIL"):
    sys.exit("out of credits")
print("Sure! " + json.dumps({k: f"Explained {k}." for k in re.findall(r"=== (T\\d+) ===", prompt)}))
"""


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text(FAKE)
    calls = tmp_path / "calls.jsonl"
    fake = Explainer("fake", "codex", "tiny", stdin=True,
                     command=lambda prompt: [sys.executable, str(script), str(calls)])
    monkeypatch.setattr(sources, "EXPLAINERS", [fake])
    monkeypatch.delenv("WHYL_EXPLAIN_WITH", raising=False)
    return fake


def calls_of(tmp_path):
    calls = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "db" / "h.db")
    store.ingest(codex.normalize(SHOP_CAPTURE))
    yield store
    store.close()


def test_turns_are_explained_once_in_one_call(tmp_path, store, fake):
    turns = explain.top_turns(store, "20261004T132159Z-39de3d")
    saved = explain.explain(store, [fake], turns)
    assert [saved[t["id"]]["text"] for t in turns] == ["Explained T1.", "Explained T2."]
    assert saved[turns[0]["id"]]["by"] == "fake (tiny)"
    [call] = calls_of(tmp_path)  # both turns in one call
    assert call["keys"] == ["T1", "T2"] and call["hooks"] == "off"
    assert call["cwd"] == str(store.explain_dir.resolve()) and call["pwd"] == str(store.explain_dir)
    assert "apply_discount in shop.py is buggy" in call["prompt"] and "-    return price - percent" in call["prompt"]
    assert "3 tests, 2 failed · AssertionError: -25 != 0" in call["prompt"]

    explain.explain(store, [fake], turns)
    assert len(calls_of(tmp_path)) == 1  # kept: never asked twice
    explain.explain(store, [fake], turns[:1], again=True)
    assert len(calls_of(tmp_path)) == 2 and calls_of(tmp_path)[-1]["keys"] == ["T1"]


def test_a_failure_is_kept_and_not_retried_on_its_own(tmp_path, store, fake, monkeypatch):
    turns = explain.top_turns(store, "20261004T132159Z-39de3d")
    monkeypatch.setenv("FAKE_FAIL", "1")
    saved = explain.explain(store, [fake], turns)
    row = saved[turns[0]["id"]]
    assert row["text"] is None and "out of credits" in row["error"]
    monkeypatch.delenv("FAKE_FAIL")
    explain.explain(store, [fake], turns)
    assert len(calls_of(tmp_path)) == 1
    assert explain.explain(store, [fake], turns, again=True)[turns[0]["id"]]["text"] == "Explained T1."


def test_a_turn_still_going_waits(tmp_path, store, fake):
    turn = dict(explain.top_turns(store, "20261004T132159Z-39de3d")[0])
    turn["status"] = "incomplete"
    last = turn["ended_at"]
    assert explain.explain(store, [fake], [turn], now=last + 60_000) == {}
    assert explain.explain(store, [fake], [turn], now=last + explain.SETTLE + 60_000)[turn["id"]]["text"]


def test_pick_prefers_the_sessions_own_agent(monkeypatch):
    def make(name, agent, installed):
        return Explainer(name, agent, None, command=lambda p: [sys.executable if installed else "no-such-cli-xyz"])
    first, own, missing = make("a", "x", True), make("b", "codex", True), make("c", "y", False)
    monkeypatch.delenv("WHYL_EXPLAIN_WITH", raising=False)
    assert explain.pick([first, own, missing], "codex") is own
    assert explain.pick([first, own, missing], "other") is first
    assert explain.pick([missing], "y") is None
    assert explain.pick([first, own], "codex", "a") is first
    monkeypatch.setenv("WHYL_EXPLAIN_WITH", "a")
    assert explain.pick([first, own], "codex") is first
    with pytest.raises(explain.ExplainError):
        explain.pick([first], "codex", "zzz")


def test_answers_are_read_leniently():
    assert explain._parse('Here you go:\n```json\n{"T1": " A. ", "T9": "x"}\n```', ["T1"]) == {"T1": "A."}
    assert explain._parse("Just prose.", ["T1"]) == {"T1": "Just prose."}
    with pytest.raises(explain.ExplainError):
        explain._parse("Just prose.", ["T1", "T2"])


def test_cli_explain_and_blame_show_it(tmp_path, store, fake, capsys):
    db = str(store.explain_dir.parent / "h.db")
    assert main(["--db", db, "blame", "/workspace/shop.py:6"]) == 0
    assert "For an AI-generated summary: whyl explain /workspace/shop.py:6  (one call to your fake CLI" \
        in capsys.readouterr().out
    assert main(["--db", db, "explain", "39de3d"]) == 0
    captured = capsys.readouterr()
    assert "Asking fake (tiny) to explain 2 turns" in captured.err
    assert "turn 1" in captured.out and "Explained T1." in captured.out and "✦ AI-generated by fake (tiny)" in captured.out
    assert main(["--db", db, "blame", "/workspace/shop.py:6"]) == 0
    out = capsys.readouterr().out
    assert "SUMMARY  ✦ AI-generated by fake (tiny) from the record below\n  Explained T1." in out
    assert "summaries ✦ AI-generated by fake (tiny)" in out and "✦ Explained T2." in out  # the rest of the session
    assert len(calls_of(tmp_path)) == 1


def test_explanation_sessions_are_never_synced(tmp_path):
    store = Store(tmp_path / "h.db")
    normalized = codex.normalize(SHOP_CAPTURE)
    normalized.session.cwd = str(store.explain_dir)
    source = sources.Source("x", 1, discover=lambda: [], matches=lambda p: True, normalize=lambda p: normalized)
    result = sources.ingest(store, tmp_path, [source])
    assert result.status == sources.SKIPPED and not store.sessions()


def test_hooks_stay_quiet_during_explanations(tmp_path, monkeypatch):
    monkeypatch.setenv(hooks.OFF, "off")
    db = tmp_path / "h.db"
    assert hooks.run("claude-code", "pre", "{not json", db) == 0
    assert not db.exists() and not (tmp_path / "hooks.log").exists()


def test_viewer_explains_on_request(tmp_path, store, fake):
    turn = explain.top_turns(store, "20261004T132159Z-39de3d")[0]
    story = view.story_view(store, "/workspace/shop.py", 6)
    assert story["explanation"] is None and story["explainer"] == "fake (tiny)"
    server = view.Server(0, str(store.explain_dir.parent / "h.db"), tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def post(headers, body):
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
            connection.request("POST", "/api/explain", body=json.dumps(body),
                               headers={"Host": f"127.0.0.1:{server.port}", **headers})
            response = connection.getresponse()
            return response.status, response.read()
        assert post({}, {"turns": [turn["id"]]})[0] == 403  # a page from another site can't send X-Whyl
        assert post({"X-Whyl": "1"}, {"turns": "all"})[0] == 400
        status, body = post({"X-Whyl": "1"}, {"turns": [turn["id"]]})
        assert status == 200 and json.loads(body)[turn["id"]]["text"] == "Explained T1."
    finally:
        server.shutdown()
        server.server_close()
    assert view.story_view(store, "/workspace/shop.py", 6)["explanation"]["by"] == "fake (tiny)"
    session = view.session_view(store, "39de3d")
    assert session["turns"][0]["explanation"]["text"] == "Explained T1." and session["turns"][1]["explanation"] is None


def test_summaries_name_the_model_that_answered(tmp_path, store, fake, monkeypatch):
    reported = Explainer(fake.name, fake.agent, fake.model, stdin=True, command=fake.command,
                         answer=lambda done: explain.Answer(done.stdout.strip(), "tiny-2"))
    turns = explain.top_turns(store, "20261004T132159Z-39de3d")
    assert explain.explain(store, [reported], turns)[turns[0]["id"]]["by"] == "fake (tiny-2)"


def test_each_cli_says_which_model_answered(tmp_path, monkeypatch):
    def done(stdout, stderr=""):
        return subprocess.CompletedProcess([], 0, stdout, stderr)
    header = "OpenAI Codex v0.1\n--------\nworkdir: /x\nmodel: gpt-test\nprovider: openai\n"
    assert codex.EXPLAINER.answer(done('{"T1": "A."}\n', header)) == explain.Answer('{"T1": "A."}', "gpt-test")
    usage = {"result": " OK ", "is_error": False, "modelUsage": {"claude-test-1": {}}}
    assert claude_code.EXPLAINER.answer(done(json.dumps(usage))) == explain.Answer("OK", "claude-test-1")
    with pytest.raises(explain.ExplainError):
        claude_code.EXPLAINER.answer(done(json.dumps({"result": "Credit balance too low", "is_error": True})))

    # opencode doesn't print the model: it is read from the saved session, which is then deleted
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "opencode.log"
    script = bin_dir / "opencode"
    script.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                      "if sys.argv[1] == 'export':\n"
                      "    print('Exporting session: x')\n"
                      "    print(json.dumps({'info': {}, 'messages': [{'info': {'role': 'assistant', 'modelID': 'pickle'}}]}))\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    events = [{"type": "step_start", "sessionID": "ses_1"}, {"type": "text", "sessionID": "ses_1", "part": {"text": "OK"}}]
    answer = opencode.EXPLAINER.answer(done("\n".join(json.dumps(e) for e in events)))
    assert answer == explain.Answer("OK", "pickle")
    assert log.read_text().splitlines() == ["export ses_1", "session delete ses_1"]
