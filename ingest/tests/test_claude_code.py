import json
import subprocess

import pytest

from recetario_ingest.config import Config, ConfigError
from recetario_ingest.llm.adapter import BudgetExceeded, MeteredLlm
from recetario_ingest.llm.claude import LlmError
from recetario_ingest.llm.claude_code import ClaudeCodeLlm
from recetario_ingest.llm.factory import build_llm
from recetario_ingest.llm.prompts import Prompt, PromptRegistry

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}


def prompts():
    registry = PromptRegistry()
    registry.register(Prompt(id="demo", version="v1", system="You extract titles.", template="Text:\n{text}"))
    return registry


def reply(**changes):
    body = {
        "type": "result", "is_error": False, "result": '{"title": "Soup"}', "structured_output": {"title": "Soup"},
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1170, "cache_read_input_tokens": 30, "output_tokens": 50},
        "total_cost_usd": 0.0004,
    } | changes
    return json.dumps(body)


class Runner:
    def __init__(self, stdout, returncode=0, stderr="", raises=None):
        self.calls, self._done, self._raises = [], subprocess.CompletedProcess([], returncode, stdout, stderr), raises

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self._raises:
            raise self._raises
        return self._done


def llm(runner, **options):
    return ClaudeCodeLlm(prompts(), runner=runner, **options)


def test_returns_the_structured_answer_with_usage_from_the_cli():
    runner = Runner(reply())
    result = llm(runner, model="haiku").structured("demo", "v1", "a soup recipe", SCHEMA)
    assert result.data == {"title": "Soup"}
    assert (result.usage.tokens_in, result.usage.tokens_out, result.usage.cost_usd) == (1202, 50, 0.0004)


def test_the_call_is_isolated_and_carries_our_prompt_and_schema():
    runner = Runner(reply())
    llm(runner, model="haiku", effort="low").structured("demo", "v1", "a soup recipe", SCHEMA)
    (command, kwargs), = runner.calls
    assert command[:2] == ["claude", "-p"]
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--system-prompt") + 1] == "You extract titles."
    assert json.loads(command[command.index("--json-schema") + 1]) == SCHEMA
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--effort") + 1] == "low"
    for flag in ("--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in command
    assert kwargs["input"] == "Text:\na soup recipe"
    assert "recetario" in kwargs["cwd"] and kwargs["cwd"] != "."  # an empty folder of its own, not the project


def test_api_key_variables_never_reach_the_cli(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    monkeypatch.setenv("HOME", "/home/someone")
    runner = Runner(reply())
    llm(runner).structured("demo", "v1", "x", SCHEMA)
    environment = runner.calls[0][1]["env"]
    assert "ANTHROPIC_API_KEY" not in environment and "ANTHROPIC_AUTH_TOKEN" not in environment
    assert environment["HOME"] == "/home/someone"  # the plan login lives under the home folder


def test_the_model_name_marks_cache_keys_as_plan_answers():
    assert llm(Runner(reply()), model="haiku").model == "claude-code:haiku"


def test_falls_back_to_the_result_text_when_there_is_no_structured_output():
    runner = Runner(reply(structured_output=None, result='{"title": "Stew"}'))
    assert llm(runner).structured("demo", "v1", "x", SCHEMA).data == {"title": "Stew"}


@pytest.mark.parametrize("message", ["Claude usage limit reached", "429 rate limit exceeded", "You've hit your limit"])
def test_a_plan_limit_stops_the_stage_instead_of_failing_every_unit(message):
    runner = Runner(reply(is_error=True, result=message, structured_output=None))
    with pytest.raises(BudgetExceeded, match="re-run later"):
        llm(runner).structured("demo", "v1", "x", SCHEMA)


def test_other_errors_fail_the_unit():
    with pytest.raises(LlmError, match="boom"):
        llm(Runner(reply(is_error=True, result="boom", structured_output=None))).structured("demo", "v1", "x", SCHEMA)
    with pytest.raises(LlmError, match="no JSON"):
        llm(Runner("not json", returncode=1, stderr="crashed")).structured("demo", "v1", "x", SCHEMA)
    with pytest.raises(LlmError, match="not JSON matching"):
        llm(Runner(reply(structured_output=None, result="plain words"))).structured("demo", "v1", "x", SCHEMA)


def test_a_missing_cli_and_a_timeout_say_what_to_do():
    with pytest.raises(LlmError, match="install Claude Code"):
        llm(Runner("", raises=FileNotFoundError())).structured("demo", "v1", "x", SCHEMA)
    with pytest.raises(LlmError, match="within"):
        llm(Runner("", raises=subprocess.TimeoutExpired("claude", 5)), timeout=5).structured("demo", "v1", "x", SCHEMA)


def test_tools_are_not_available():
    with pytest.raises(NotImplementedError):
        llm(Runner(reply())).structured("demo", "v1", "x", SCHEMA, tools=("web_search",))


def test_the_factory_builds_it_without_a_price_and_checks_the_cli_exists(monkeypatch):
    config = Config(provider="claude-code", model="haiku", max_cost_usd=1.0)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    built = build_llm(config, prompts())
    assert isinstance(built, MeteredLlm) and built.model == "claude-code:haiku"
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(ConfigError, match="Claude Code CLI"):
        build_llm(config, prompts())
