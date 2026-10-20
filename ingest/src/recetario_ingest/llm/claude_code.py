"""A provider that asks Claude through the Claude Code CLI (`claude -p`), using the person's plan instead of an API key.

Each call is one `claude -p` process: our system prompt replaces the default one, tools and slash commands are off, nothing is
saved, and it runs in an empty folder so no project files or memory leak into the answer. Anything that would make the CLI
bill an API key is removed from its environment, so the call always goes through the plan login.

The cost it reports is the CLI's own list-price estimate. It is not billed on a plan; the spending cap then works as a
rough guard on how much of the plan a run uses.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import weakref
from collections.abc import Callable, Sequence

from recetario_ingest.llm.adapter import BudgetExceeded, LlmResult, Usage
from recetario_ingest.llm.claude import LlmError
from recetario_ingest.llm.prompts import PromptRegistry

# With either of these set the CLI would use the API (and bill it) instead of the plan login.
_BILLING_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_LIMIT = re.compile(r"limit|quota|rate.?limit|too many requests|overloaded|usage", re.IGNORECASE)

Runner = Callable[..., subprocess.CompletedProcess]


class ClaudeCodeLlm:
    def __init__(
        self,
        prompts: PromptRegistry,
        model: str = "haiku",
        effort: str | None = None,
        timeout: float = 300,
        runner: Runner = subprocess.run,
        executable: str = "claude",
    ) -> None:
        self.model = f"claude-code:{model}"  # part of every cache key, so API and plan answers never mix
        self._cli_model = model
        self._prompts = prompts
        self._effort = effort
        self._timeout = timeout
        self._runner = runner
        self._executable = executable
        self._workdir = tempfile.mkdtemp(prefix="recetario-claude-")
        weakref.finalize(self, shutil.rmtree, self._workdir, ignore_errors=True)

    def structured(self, prompt_id: str, version: str, input: str, schema: dict, tools: Sequence[str] = ()) -> LlmResult:
        if tools:
            raise NotImplementedError("tool use isn't available through the Claude Code provider")
        prompt = self._prompts.get(prompt_id, version)
        command = [
            self._executable, "-p", "--model", self._cli_model, "--output-format", "json", "--json-schema", json.dumps(schema),
            "--system-prompt", prompt.system, "--tools", "", "--disable-slash-commands", "--strict-mcp-config",
            "--no-session-persistence", "--exclude-dynamic-system-prompt-sections",
        ]
        if self._effort:
            command += ["--effort", self._effort]
        environment = {key: value for key, value in os.environ.items() if key not in _BILLING_VARIABLES}
        try:
            done = self._runner(
                command, input=prompt.template.format(text=input), capture_output=True, text=True, encoding="utf-8",
                cwd=self._workdir, env=environment, timeout=self._timeout,
            )
        except FileNotFoundError as error:
            raise LlmError(f"the {self._executable} command was not found; install Claude Code and sign in") from error
        except subprocess.TimeoutExpired as error:
            raise LlmError(f"claude did not answer within {self._timeout:.0f} seconds") from error

        try:
            reply = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise LlmError(f"claude exited with {done.returncode} and no JSON: {(done.stderr or done.stdout).strip()[:300]}") from None
        if reply.get("is_error") or done.returncode != 0:
            message = str(reply.get("result") or reply.get("subtype") or done.stderr).strip()[:300]
            if reply.get("api_error_status") == 429 or _LIMIT.search(message):
                raise BudgetExceeded(f"the plan's usage limit looks reached ({message}); re-run later, finished units are cached")
            raise LlmError(f"claude reported an error: {message}")

        data = reply.get("structured_output")
        if not isinstance(data, dict):
            try:
                data = json.loads(reply.get("result") or "")
            except json.JSONDecodeError:
                raise LlmError("claude's reply was not JSON matching the schema") from None
        used = reply.get("usage") or {}
        usage = Usage(
            tokens_in=int(used.get("input_tokens", 0)) + int(used.get("cache_creation_input_tokens", 0)) + int(used.get("cache_read_input_tokens", 0)),
            tokens_out=int(used.get("output_tokens", 0)),
            cost_usd=float(reply.get("total_cost_usd") or 0.0),
        )
        return LlmResult(data=data, usage=usage)
