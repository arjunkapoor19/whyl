# Development

Requires [uv](https://docs.astral.sh/uv/). whyl has no runtime dependencies beyond the Python standard library (3.9+).

```sh
uv sync
uv run pytest -q                                      # tests
uv run --isolated --python 3.9 --group dev pytest -q  # on Python 3.9
uvx pyflakes whyl tests                               # unused code
uv run whyl --db /tmp/scratch.db log                  # experiment on a scratch database, not your real history
```

## Layout

```
recorder/codex_recorder.py   records one Codex session (stdlib-only Python 3.9+)
whyl/                        canonical model, SQLite store, blame, `whyl` CLI
  sources.py                 every source of agent history, and syncing them
  adapters/codex.py          Codex recorder capture -> canonical events
  adapters/codex_rollout.py  Codex's own session logs -> canonical events
  adapters/claude_code.py    Claude Code session transcripts -> canonical events
  adapters/opencode.py       opencode's session database -> canonical events
  view.py                    `whyl view`: a local server and JSON API for the viewer
  explain.py                 `whyl explain`: AI-generated turn summaries, by an agent CLI you have
  static/                    the viewer's built page (generated from web/, committed)
web/                         the viewer's source: Vite + React + Tailwind
tests/                       pytest suite; fixtures/ holds sanitized real captures
codex-schema/                App Server schemas generated from the installed Codex
docs/event-model.md          the canonical model, each agent's mapping, and how blame works
docs/protocol-notes.md       what we've learned about the Codex App Server protocol from real captures
docs/recorder.md             the optional Codex recorder and its capture format
captures/                    one directory per recorded session (gitignored; may contain code and prompts)
```

Only `sources.py` and `adapters/` may name an agent. Adding an agent means writing an adapter that produces the canonical model in `whyl/model.py` and registering it in `sources.py`; see `docs/event-model.md`.

## The viewer

Working on the viewer (`web/`) needs Node 20+:

```sh
uv run whyl view --no-open                    # the API, on 127.0.0.1:4545
cd web && npm ci && npm run dev               # the page with hot reload, sending /api to whyl view
npm test                                      # frontend unit tests
npm run build                                 # rebuild whyl/static/ (commit the result)
```

The build fails if the first paint needs more than 120 KB of JavaScript (gzipped), and the Python tests fail if `web/` changed without a rebuild.
