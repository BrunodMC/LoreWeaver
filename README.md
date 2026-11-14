# LoreWeaver

A co-DM for a [Daggerheart](https://www.daggerheart.com/) table. It keeps the campaign's **world bible**
(NPCs, locations, factions, arcs, items, adversaries, PCs and the session log) and uses Claude to
draft new entries grounded in everything already written.

- **Browse and edit the bible.** Every entry has a status (draft, canon, archived) and a
  known-to-party flag. Text wrapped in `||double bars||` is a DM-only secret. The **Party view**
  shows only canon entries the party knows, with secrets removed.
- **Generate drafts.** *✦ Generate* takes a type, a brief and optional **anchors** (entries the new
  one must link to). Claude reads the rules, the campaign frame and the whole bible, and drafts an
  entry that lands as a draft, unknown to the party. *✦ Spin off* on any entry opens the form
  already anchored to it.
- **Review before anything is canon.** An AI draft shows a review panel: proposed links to tick,
  a warning when it proposes answers to open questions, *Regenerate with notes* (fields the notes
  don't touch are kept exactly as they are), *Discard*, and a version history you can step through
  and restore. *Drafts to review* lists everything waiting.

Nothing the AI writes becomes canon, or reaches the players, without the DM choosing it.

## Setup

Needs Python 3.12+ and [Claude Code](https://claude.com/claude-code), logged in. Generation runs
through the headless `claude` CLI on your own Claude subscription, so there's no API key to manage.

```powershell
pip install -r requirements.txt
claude          # once, to log in, if you haven't
```

## Run

From the project root:

```powershell
python -m uvicorn src.app.main:app --reload
```

Then open <http://127.0.0.1:8000>. On startup the app backs up the database to `data/backups/`
(keeping the last ten) and applies any pending migrations.

## Where the data lives

The bible is a SQLite file at `data/loreweaver.db`. The `data/` folder is gitignored on purpose:
**this repository is public**, so campaign lore, secrets and player details live only in the
database, never in committed files. The context modules in `src/context/` hold only published
rules and frame material.

| Variable | What it does |
|---|---|
| `LOREWEAVER_DB` | Use a different database file. **Set it when running from a git worktree**: `data/` isn't copied into worktrees, so without it the app quietly starts an empty bible there. |
| `LOREWEAVER_CLAUDE_BIN` | Path to the `claude` executable, if it isn't found on `PATH`. On Windows the app skips npm's `claude.cmd` shim and calls the native `claude.exe` next to it. |

Running a branch checked out in a worktree, against the real bible:

```powershell
$env:LOREWEAVER_DB = "C:\path\to\LoreWeaver\data\loreweaver.db"
python -m uvicorn src.app.main:app --reload
```

## Tests

```powershell
python -m pytest -q
```

Tests run against a temporary database and a scripted fake model: no test can call the real
`claude` CLI or spend subscription usage.

## Layout

| Path | What's there |
|---|---|
| `src/app/db.py`, `migrations/` | Schema, migrations, backups and every write to the bible |
| `src/app/entity_types.py` | Entry types and their fields |
| `src/app/knowledge.py`, `search.py` | `\|\|secret\|\|` handling and party-safe search |
| `src/app/llm.py` | The one door to the model (`claude -p` backend, fake backend for tests) |
| `src/app/context.py` | What the model sees (prompt, bible rendering, output schema) and the checks on its answers |
| `src/app/generation.py` | Running generations as background jobs, versions, promote and restore |
| `src/app/routes/` | Pages: browsing and editing, generation, review |
| `src/context/` | Rules and campaign-frame modules fed to the model |
