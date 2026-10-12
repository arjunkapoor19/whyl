# whyl

**Git blame for AI coding agents.** Git tells you *what* changed. whyl tells you *why the agent changed it*: the prompt it was working on, what it saw just before, the edit itself, and whether it ever checked its work.

<!-- demo: docs/demo.gif -->

Works with **Claude Code**, **Codex** and **opencode**. Local-first: one SQLite file on your machine, no accounts, no runtime dependencies. Get explanations of code written by your agent without any external keys or accounts.

## The diff looks fine. Was it?

Agents write more and more of your code, and `git diff` only shows the result. It can't tell you that a line was written after two failing test runs, that the agent edited the test instead of the code, or that nothing ever ran after the change. whyl reads the history your agents already keep and answers one question for any line: **why does this exist?**

```text
$ whyl blame shop.py:8
shop.py:8
        return price * (1 - percent / 100)

written by codex · session 20261004T132159Z-39de3d · turn 1 · 2026-10-04 18:52:33 IST

YOU ASKED
  apply_discount in shop.py is buggy. Write unittest tests in test_shop.py that expose it, run them
  and watch them fail, then fix the bug and rerun until they pass.

WHY THIS CHANGE  what the record shows just before it
  said     18:52:26  The first run exposed one bug, but the 20% test used a $100 price, which
                     accidentally passed the current subtraction implementation. I'm strengthening
                     that case with a different price so it checks percentage math directly, then
                     I'll correct `apply_discount`.
  failing  18:52:30  ✗ python3 -m unittest -v  → 3 tests, 2 failed · AssertionError: -25 != 0

THE CHANGE  18:52:33  shop.py +1 −1
  │ @@ -5,3 +5,3 @@
  │  def apply_discount(price, percent):
  │ -    return price - percent
  │ +    return price * (1 - percent / 100)   ← this line
  Then, 1s later: ✓ python3 -m unittest -v  → 3 tests passed

WORKED  for 32s · 12 steps · test_shop.py +19 −1 · shop.py +1 −1 · tests ✗ ✗ ✓
  18:52:08  agent    I'll inspect `shop.py` and the existing test setup, add focused `unittest` cas…
  18:52:20  edit     test_shop.py +18 −0
  18:52:23  test     ✗ python3 -m unittest -v  → 3 tests, 1 failed · AssertionError: -25 != 0
  …
▶ 18:52:33  edit     shop.py +1 −1   ← wrote this line
  18:52:34  test     ✓ python3 -m unittest -v  → 3 tests passed

THE AGENT CONCLUDED  18:52:35
  Added three `unittest` cases for percentage, zero, and full discounts in test_shop.py. …
```

Everything above comes from the agent's own record. whyl shows the order in which things happened, not proof of cause, and says so.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.9+.

```sh
uv tool install git+https://github.com/arjunkapoor19/whyl
whyl setup     # one time setup
```

Then use your agents exactly as before. whyl picks up new sessions on its own.

## What you get

**`whyl blame FILE`**: who wrote every line, when several agents (and you) share a file.

```text
$ whyl blame shop.py
author   session turn when               #  code
                                         7  def apply_discount(price, percent):
codex    39de3d  t1   Oct 04 '26 18:52   8      return price * (1 - percent / 100)
…
opencode 6T6Mlw  t2   Oct 11 '26 00:15  17  def mean(prices):
codex    39de3d  t2   Oct 04 '26 18:53  18      if not prices:
codex    39de3d  t2   Oct 04 '26 18:53  19          return 0.0
…
claude   f510a2  t1   Oct 07 '26 15:13  46  def multiply_arrays(a, b):
claude   f510a2  t1   Oct 07 '26 15:13  47      if len(a) != len(b):
claude   f510a2  t1   Oct 07 '26 15:13  48          raise ValueError("arrays must have the same length")
claude   f510a2  t1   Oct 07 '26 15:13  49      return [x * y for x, y in zip(a, b)]

19 pre-existing · 18 codex · 14 claude · 8 opencode · 4 untracked
```

**`whyl blame FILE:LINE`**: the story of the turn that wrote that line, as shown above. What you asked, why the change was made as far as the record shows, the diff with the line marked, the test runs around it, every step of the turn and what the agent concluded. Sub-agent hand-offs are followed back to the prompt that started them.

**`whyl view [FILE[:LINE]]`**: the same history in your browser. Each file is shown with who wrote every block; click any line for its story. It updates live while an agent works. `j`/`k` move between lines, `⌘K` finds any file or session. The page is served from 127.0.0.1 only, and it's prebuilt, so you don't need Node.

<!-- screenshot: docs/viewer.png -->

**`whyl explain FILE:LINE`**: a plain-language summary of a turn, written by an agent CLI you already have (`claude`, `codex` or `opencode`, run headless with no tools). A session goes to its own agent's CLI, so your code reaches no provider that hadn't already seen it. Each turn is summarized once and kept, nothing is summarized unless you ask, and every summary is labelled as AI-generated, with the model that wrote it.

**`whyl log [SESSION]`**: every session, or one session's timeline.

## Supported agents

| Agent | History read from | Shell edits (`sed -i`, `echo >>`) |
|---|---|---|
| Claude Code | `~/.claude/projects` transcripts | with `whyl setup` (hooks in `~/.claude/settings.json`) |
| Codex | `~/.codex/sessions` logs, or the optional [recorder](docs/recorder.md) | with `whyl setup` (hooks in `~/.codex/hooks.json`) |
| opencode | its SQLite database, read-only | with `whyl setup` (plugin in `~/.config/opencode/plugin/whyl.js`) |

Agents don't report edits they make through the shell. `whyl setup` adds small hooks that snapshot the project before and after each tool call (about 0.1 s each), so every change is credited to the call that made it. Edits made between tool calls are labelled `you`. Your settings are backed up as `*.whyl.bak`, and `whyl setup --remove` takes the hooks out again. Without setup, whyl still works from the agents' logs alone.

Adding an agent means writing one adapter into an agent-agnostic event model; see [docs/event-model.md](docs/event-model.md).

## Local and private

- Everything stays on your machine: the database is `~/.whyl/history.db` (override with `--db` or `$WHYL_DB`), and snapshots of text files up to 1 MB live next to it in `~/.whyl/objects`.
- whyl only reads your agents' history. It keeps synced sessions even after an agent deletes its own (Claude Code removes transcripts after about 30 days).
- Nothing leaves your machine unless you run `whyl explain`, and then only through an agent CLI you already use.
- Blame aligns everything with the file on disk, so a line nothing recorded is reported as untracked, never misattributed.

## Roadmap

- **Behavioral diffs:** compare how agents behave across runs, models and prompts (steps, tests run, files touched, tokens, retries).
- Sub-agent transcripts for Claude Code, and OpenTelemetry ingest for agents beyond coding.

## Docs

- [docs/event-model.md](docs/event-model.md): the canonical event model, each agent's mapping, and how blame works
- [docs/development.md](docs/development.md): running the tests, the code layout, and working on the viewer

## License

[MIT](LICENSE)
