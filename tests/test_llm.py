import json
import subprocess
from pathlib import Path

import pytest

from src.app import llm

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}


def cli_payload(**overrides) -> str:
    payload = {
        "type": "result", "subtype": "success", "is_error": False,
        "structured_output": {"name": "Mara"},
        "modelUsage": {"claude-opus-5-5": {"inputTokens": 2}},
        "usage": {"input_tokens": 2, "output_tokens": 40},
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_success():
    result = llm.parse_cli_output(0, cli_payload(), "")
    assert result.data == {"name": "Mara"}
    assert result.model == "claude-opus-5-5"
    assert result.usage["output_tokens"] == 40


@pytest.mark.parametrize("stdout, message", [
    (cli_payload(is_error=True, subtype="error_during_execution", result="Credit low"), "Credit low"),
    (cli_payload(structured_output=None), "without the structured output"),
    ("Not logged in", "exited with code 1"),
    ("", "no output"),
])
def test_parse_failures_raise_llm_error(stdout, message):
    with pytest.raises(llm.LLMError, match=message):
        llm.parse_cli_output(1, stdout, "")


def test_command_isolates_generation_from_the_dms_claude_setup():
    cmd = llm.ClaudeCLIBackend(executable="claude").command(Path("sys.md"), SCHEMA)
    joined = " ".join(cmd)
    assert "--bare" not in cmd  # --bare ignores subscription auth
    for flag in ("--system-prompt-file", "--tools", "--setting-sources", "--strict-mcp-config",
                 "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == llm.DEFAULT_MODEL
    assert cmd[cmd.index("--effort") + 1] == "medium"
    deep = llm.ClaudeCLIBackend(executable="claude").command(Path("sys.md"), SCHEMA, deep=True)
    assert deep[deep.index("--effort") + 1] == "high"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    assert "sys.md" in joined


def test_generate_sends_system_via_file_and_prompt_via_stdin(monkeypatch):
    seen = {}

    def fake_run(cmd, input, cwd, **kwargs):
        system_file = Path(cmd[cmd.index("--system-prompt-file") + 1])
        seen.update(system=system_file.read_text(encoding="utf-8"), prompt=input, cwd=cwd)
        assert system_file.parent == Path(cwd)  # scratch dir, away from any project CLAUDE.md
        return subprocess.CompletedProcess(cmd, 0, cli_payload(), "")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    big_system = "Ærth lore. " * 5000  # well past the Windows command-line limit
    result = llm.ClaudeCLIBackend(executable="claude").generate(big_system, "a ferrywoman", SCHEMA)
    assert result.data == {"name": "Mara"}
    assert seen["system"] == big_system and seen["prompt"] == "a ferrywoman"
    assert not Path(seen["cwd"]).exists()  # scratch dir cleaned up


def test_timeouts_and_missing_binary_become_llm_errors(monkeypatch):
    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(llm.subprocess, "run", timeout)
    with pytest.raises(llm.LLMError, match="didn't answer"):
        llm.ClaudeCLIBackend(executable="claude", timeout=1).generate("s", "p", SCHEMA)

    def missing(cmd, **kwargs):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(llm.subprocess, "run", missing)
    with pytest.raises(llm.LLMError, match="Couldn't start"):
        llm.ClaudeCLIBackend(executable="claude").generate("s", "p", SCHEMA)


def test_executable_lookup_skips_the_npm_cmd_shim(tmp_path, monkeypatch):
    monkeypatch.delenv("LOREWEAVER_CLAUDE_BIN", raising=False)
    shim = tmp_path / "claude.CMD"
    shim.write_text("@ECHO off")
    monkeypatch.setattr(llm.shutil, "which", lambda name: str(shim))
    with pytest.raises(llm.LLMError, match="LOREWEAVER_CLAUDE_BIN"):
        llm.find_claude_executable()

    native = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    native.parent.mkdir(parents=True)
    native.write_text("")
    assert llm.find_claude_executable() == str(native)

    monkeypatch.setenv("LOREWEAVER_CLAUDE_BIN", "C:/elsewhere/claude.exe")
    assert llm.find_claude_executable() == "C:/elsewhere/claude.exe"

    monkeypatch.delenv("LOREWEAVER_CLAUDE_BIN")
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    with pytest.raises(llm.LLMError, match="on PATH"):
        llm.find_claude_executable()


def test_fake_backend_scripts_answers_and_records_calls():
    fake = llm.FakeBackend([{"name": "A"}, lambda s, p, schema: {"name": p.upper()}, llm.LLMError("boom")])
    llm.set_backend(fake)
    try:
        assert llm.generate("sys", "x", SCHEMA).data == {"name": "A"}
        assert llm.generate("sys", "brief", SCHEMA).data == {"name": "BRIEF"}
        with pytest.raises(llm.LLMError, match="boom"):
            llm.generate("sys", "y", SCHEMA)
        with pytest.raises(llm.LLMError, match="ran out"):
            llm.generate("sys", "z", SCHEMA)
        assert [c["prompt"] for c in fake.calls] == ["x", "brief", "y", "z"]
    finally:
        llm.set_backend(None)
    assert isinstance(llm.get_backend(), llm.ClaudeCLIBackend)
