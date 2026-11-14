"""The one door LLM calls go through.

Callers hand over a system prompt, a user prompt and a JSON schema, and get back a dict that matches
the schema. Which model answers, and over what transport, stays in here: today the headless Claude Code
CLI on the DM's subscription; tomorrow maybe the API or a local model, behind the same `generate`.
"""
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = "claude-opus-5-5"  # pinned, not the "opus" alias, so an upgrade is a decision, not a surprise
DEFAULT_EFFORT = "high"            # Opus 5.5 defaults to medium; grounded creative work earns more thought
DEFAULT_TIMEOUT = 300              # seconds; prep time, but a hung subprocess shouldn't hang the app forever


class LLMError(RuntimeError):
    """The model couldn't produce a usable answer (transport failure, refusal, missing structured output)."""


@dataclass
class GenerationResult:
    data: dict
    model: str
    usage: dict | None = None


class Backend(Protocol):
    def generate(self, system: str, prompt: str, schema: dict) -> GenerationResult: ...


# ── Claude Code CLI (subscription auth) ────────────────────────────────────

def find_claude_executable() -> str:
    """Locate the claude binary, skipping npm's .cmd shim.

    Passing a JSON schema through cmd.exe risks mangled quotes, so on Windows we call the native
    claude.exe the shim forwards to. LOREWEAVER_CLAUDE_BIN overrides the lookup.
    """
    override = os.environ.get("LOREWEAVER_CLAUDE_BIN")
    if override:
        return override
    found = shutil.which("claude")
    if found is None:
        raise LLMError("Couldn't find the `claude` CLI on PATH. Install Claude Code or set LOREWEAVER_CLAUDE_BIN.")
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".bat"):
        native = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if native.exists():
            return str(native)
        raise LLMError(f"`claude` resolves to a {path.suffix} shim and its native binary wasn't found next to it. "
                       "Set LOREWEAVER_CLAUDE_BIN to the claude executable.")
    return found


@dataclass
class ClaudeCLIBackend:
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    timeout: float = DEFAULT_TIMEOUT
    executable: str | None = None

    def command(self, system_prompt_file: Path, schema: dict) -> list[str]:
        return [
            self.executable or find_claude_executable(),
            "-p",
            # The DM's own Claude Code setup (CLAUDE.md, memory, hooks, MCP servers, tools) must not leak
            # into generation, so every source of it is switched off and the system prompt replaced.
            "--system-prompt-file", str(system_prompt_file),
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--model", self.model,
            "--effort", self.effort,
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
        ]

    def generate(self, system: str, prompt: str, schema: dict) -> GenerationResult:
        # The system prompt (rules + the whole bible) goes through a file and the brief through stdin:
        # Windows caps a command line at ~32k characters and the bible alone is bigger than that.
        # A scratch working directory keeps any project CLAUDE.md out of reach.
        with tempfile.TemporaryDirectory(prefix="loreweaver-") as scratch:
            system_file = Path(scratch) / "system.md"
            system_file.write_text(system, encoding="utf-8")
            try:
                completed = subprocess.run(
                    self.command(system_file, schema), input=prompt, capture_output=True,
                    text=True, encoding="utf-8", timeout=self.timeout, cwd=scratch,
                )
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"Claude didn't answer within {self.timeout:.0f}s.") from exc
            except OSError as exc:
                raise LLMError(f"Couldn't start the claude CLI: {exc}") from exc
        return parse_cli_output(completed.returncode, completed.stdout, completed.stderr)


def parse_cli_output(returncode: int, stdout: str, stderr: str) -> GenerationResult:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        detail = (stderr or stdout).strip()[:500]
        raise LLMError(f"The claude CLI exited with code {returncode}: {detail or 'no output'}") from None
    if payload.get("is_error") or payload.get("subtype") != "success":
        detail = payload.get("result") or payload.get("api_error_status") or payload.get("subtype")
        raise LLMError(f"Claude reported an error: {detail}")
    data = payload.get("structured_output")
    if not isinstance(data, dict):
        raise LLMError("Claude answered without the structured output the schema asked for.")
    models = list(payload.get("modelUsage") or {})
    return GenerationResult(data=data, model=models[0] if models else "unknown", usage=payload.get("usage"))


# ── Fake backend for tests ─────────────────────────────────────────────────

@dataclass
class FakeBackend:
    """Answers from a script: canned dicts, or callables that build one from (system, prompt, schema)."""
    responses: list[dict | Callable[[str, str, dict], dict] | Exception] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    model: str = "fake-model"

    def generate(self, system: str, prompt: str, schema: dict) -> GenerationResult:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise LLMError("FakeBackend ran out of scripted responses.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        data = response(system, prompt, schema) if callable(response) else response
        return GenerationResult(data=data, model=self.model, usage={"output_tokens": 0})


# ── The door ───────────────────────────────────────────────────────────────

_backend: Backend | None = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = ClaudeCLIBackend()
    return _backend


def set_backend(backend: Backend | None) -> None:
    """Swap the backend (tests use FakeBackend); None restores the default on next use."""
    global _backend
    _backend = backend


def generate(system: str, prompt: str, schema: dict) -> GenerationResult:
    return get_backend().generate(system, prompt, schema)
