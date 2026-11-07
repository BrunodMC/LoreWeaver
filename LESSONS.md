# Lessons from the AI Sandbox (Aug–Nov 2026)

What this sandbox learned about running agents with local models, written to be reused
in LoreWeaver. Each item says what happened and what to do about it. The full history
lives in this repo's git log and `OPENHANDS_EVAL_REPORT.md`.

**The split these lessons assume:** cloud models (Claude Code) write the code; local
models (Ollama on an RTX 3060, 12GB) run *inside* the app for creative and operational
work: NPC agents, note lookup, lore generation.

---

## 1. What small local models are good and bad at

- **Good reasoning, sloppy execution.** Across 11 OpenHands runs and a replay, qwen 14B's
  *plan* was right every time: sensible tool ideas, correct game logic. What failed was
  mechanical: stripped quotes, missing imports, edits inserted at the wrong line, code
  placed after a blocking call, single-sample tests.
- **They don't read rejections.** When a guard blocked an edit and explained why, the
  model resent the identical edit (24 times in a row in one run), even after viewing
  the correct file. Escalating the message, or pasting in the real file content,
  didn't help. Detection works; correction-by-feedback doesn't.
- **Literal instruction leakage.** qwen2.5-coder copied the phrase "no docstring" from a
  prompt into a function name. Phrase constraints as plain prose ("the test must not
  include a docstring"), not as fragments that read like content.
- **qwen3 vs qwen2.5-coder:** qwen3:14b thinks out loud by default, so it spends ~5x the
  tokens and is slower; qwen2.5-coder:14b is terser and faster but more literal. Swapping
  models on a 12GB GPU costs a reload, so settle on one default per job.

**Design rule for LoreWeaver:** give local models **narrow, structured outputs** that
Python validates. An NPC agent picks from a short menu ("look up X", "act: Y", "say: Z")
as JSON, and code checks the choice before acting on it. Never hand a small model
open-ended actions, like file edits or free-form tool chains, and hope.

## 2. Ollama gotchas (each cost real debugging time)

- **Context silently capped at 4096 tokens.** litellm's Ollama provider sends its own
  `num_ctx`, overriding the server's `OLLAMA_CONTEXT_LENGTH`. Set it explicitly per request
  (`options: {num_ctx: 32768}`) and confirm with `ollama ps`. Symptom: the model "forgets"
  the start of long prompts.
- **Native tool-calling is unreliable for qwen2.5-coder through Ollama.** Tool calls came
  back as JSON *text* in the message, so nothing ran. Prompt-based action parsing
  (`native_tool_calling: false` in OpenHands) fixed it. Expect the same in any framework:
  verify that tool calls actually execute, not just that the run "finished".
- **`reasoning_effort` must be off for non-thinking models.** Cloud-oriented defaults like
  `"high"` make Ollama reject qwen2.5-coder outright. Use `"none"` (the string; a null was
  silently ignored).
- **Inside Docker, reach host Ollama at `http://host.docker.internal:11434`.**

## 3. Agent frameworks

- **CrewAI doesn't enforce observe-before-next-action.** In `resolve_npc_action.py`, the
  agent fired both dice rolls before reading either result, so "follow up only if the
  first roll misses" never actually branched; it narrated around the results. If a
  decision depends on a previous result, **own the loop in Python**: one model call, read
  the result, then the next call. Don't trust the framework to sequence it.
- **Ambiguous prompts produce ambiguous runs.** The same script stated the miss as a fact
  *and* asked for a roll to determine it. Every decision point needs one clear source of truth.
- **CrewAI's cloud tracing (app.crewai.com)** uploads trace metadata, not inference. It was
  left on deliberately as a results viewer; decide consciously in LoreWeaver.
- **Aider** (supervised edits, local models): solid for bounded edits with auto-lint and
  auto-test, but it has no self-directed loop. Gotcha: it `git add`s new files *before*
  writing them, so check staged content before committing after an Aider session.
- **OpenHands Agent Canvas** (REST API, Docker): works with local models given the Ollama
  settings above. Its `.openhands/hooks.json` is **not** auto-loaded over the REST API; pass
  `hook_config` in every conversation-start request.

## 4. MCP tool servers

- **Write MCP servers in pure Python (FastMCP).** On Windows, Python's asyncio/anyio cannot
  reliably spawn Node subprocesses (npx cache corruption, `.cmd` shims, silent
  `Connection closed`). Don't use npm-based MCP servers from Python on this machine.
- **Scope tools tightly.** `dnd_notes_server.py`'s `_safe_path()` keeps reads inside the
  notes folder, and the agent only gets `list_directory` / `read_file` / `search_notes`,
  all read-only. That's the pattern for NPC agents: they can look things up but
  can't wander or write.
- **Put `if __name__ == "__main__": mcp.run()` at the very end of the file.** `mcp.run()`
  blocks, so any `@mcp.tool()` defined after it never registers when the server runs
  over stdio. Tests that import the module **won't catch this**. (A real tool was
  silently missing from the dice server for six weeks.)
- **Retrieval was naive** (whole files into context). Fine for a few notes, but LoreWeaver
  will need chunking or embeddings once the lore grows.

## 5. Trusting agent output (applies to cloud agents too)

- **"Tests pass" is not "it works".** A qwen run passed pytest while its new tool was
  unreachable at runtime, had dead code and had a single-sample test. Check the
  things the tests can't see: structure, registration, unchanged existing tests.
- **Graders must be tested against cheating.** The agent runner's grader has tests for
  each known failure (weakened tests, a stripped decorator, a tool swallowed into a
  string, stray files). A grader nobody has tried to fool will be fooled.
- **Keep artifacts and regrade.** Saving each run's patch and transcript let old runs be
  re-scored when the grader improved, which turned one "PASS" into a "FAIL" without rerunning.
- **Comparison result (Nov 2026):** on the same task, Claude Sonnet and Haiku passed in ~7
  turns / ~30s. qwen 14B with guardrails passed cleanly in 2 of 11 runs, and its best
  replay failed the stricter grader. This is why code is written by cloud models.

## 6. Claude Code specifics

- **Auto mode blocks Claude from launching other agents** (Aider, OpenHands) under "Create
  Unsafe Agents", and from granting itself permission ("Self-Modification"). A narrow
  `permissions.allow` rule added *by the user* in `.claude/settings.local.json` clears it.
- **Headless `claude -p`** (CLI 2.1.318): bills the subscription unless `ANTHROPIC_API_KEY`
  is set; has no `--max-turns`; `--settings` permission rules and `--permission-prompts none`
  work for unattended runs; the agent's shell ignores an injected PATH, so pass interpreter
  paths explicitly.
- **Claude Code's memory is per project folder.** Lessons only carry between repos as
  files like this one, not as memories.

## 7. Process

- **Pin goals to something testable.** The sandbox's "generic agency capability" had no
  finish line, so the goals kept moving. Tie each piece of work to something you can
  check: a feature, a test, a session at the table.
- **Verify tools against their real docs or `--help`, not blog posts or memory.** Wrong
  assumptions found this way: OpenHands' CLI was deprecated, `claude` had no
  `--max-turns`, and the OpenHands API's request shape was unrecorded.
- **One honest negative result beats three inconclusive ones.** Several runs were stopped
  once the trend was clear (e.g. per-turn latency climbing 210s → 298s).
