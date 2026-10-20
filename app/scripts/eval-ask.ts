// Scores the natural-language search: 20 questions in evals/nl/cases.json, each with the filters it should produce.
//   pnpm eval:ask                    through the Claude Code CLI, on your plan (no API key)
//   pnpm eval:ask --api              through the API, the way the proxy calls it (needs ANTHROPIC_API_KEY)
// A case passes when the ingredient names, the limits and the "ignored" flag are all as expected.

import Anthropic from "@anthropic-ai/sdk";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

import { costUsd, interpret, MODEL } from "../src/nl/interpret";
import { ASK_SYSTEM, ASK_TOOL, parseAsk, type Ask } from "../src/nl/spec";
import { fold } from "../src/text";

interface Case {
  id: string;
  question: string;
  have: string[][]; // each item: the accepted spellings of one ingredient
  exclude: string[][];
  maxMissing: number;
  maxTotalMin: number | null;
  ignored: boolean;
}

const cases: Case[] = JSON.parse(readFileSync(new URL("../../evals/nl/cases.json", import.meta.url), "utf8"));
const useApi = process.argv.includes("--api");

interface Answer { ask: Ask | null; usd: number; seconds: number }

async function viaApi(question: string): Promise<Answer> {
  const started = Date.now();
  const result = await interpret(new Anthropic(), question);
  return { ask: result.ask, usd: costUsd(result.inputTokens, result.outputTokens), seconds: (Date.now() - started) / 1000 };
}

function viaCli(question: string): Answer {
  const started = Date.now();
  const env = { ...process.env };
  delete env.ANTHROPIC_API_KEY; // the point is to bill the plan, not the key
  delete env.ANTHROPIC_AUTH_TOKEN;
  const run = spawnSync(
    "claude",
    ["-p", "--model", "haiku", "--output-format", "json", "--json-schema", JSON.stringify(ASK_TOOL.input_schema),
      "--system-prompt", ASK_SYSTEM, "--tools", "", "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence",
      "--exclude-dynamic-system-prompt-sections", "--effort", "low"],
    { input: question, encoding: "utf8", env, timeout: 120_000 },
  );
  let ask: Ask | null = null;
  let usd = 0;
  try {
    const out = JSON.parse(run.stdout);
    usd = out.total_cost_usd ?? 0;
    ask = parseAsk(out.structured_output ?? JSON.parse(out.result));
  } catch {
    ask = null;
  }
  return { ask, usd, seconds: (Date.now() - started) / 1000 };
}

const key = (term: string) => fold(term).replace(/s$/, "");
const covers = (found: string[], wanted: string[][]) =>
  wanted.length === found.length && wanted.every((spellings) => found.some((term) => spellings.some((spelling) => key(term) === key(spelling))));

function problems(ask: Ask | null, expected: Case): string[] {
  if (ask === null) return ["no tool call"];
  const out: string[] = [];
  if (!covers(ask.have, expected.have)) out.push(`have ${JSON.stringify(ask.have)}`);
  if (!covers(ask.exclude, expected.exclude)) out.push(`exclude ${JSON.stringify(ask.exclude)}`);
  if (ask.maxMissing !== expected.maxMissing) out.push(`maxMissing ${ask.maxMissing}`);
  if ((ask.maxTotalMin ?? null) !== expected.maxTotalMin) out.push(`maxTotalMin ${ask.maxTotalMin ?? "none"}`);
  if ((ask.ignored.length > 0) !== expected.ignored) out.push(`ignored ${JSON.stringify(ask.ignored)}`);
  return out;
}

let passed = 0;
let usd = 0;
let seconds = 0;
for (const item of cases) {
  const answer = useApi ? await viaApi(item.question) : viaCli(item.question);
  const wrong = problems(answer.ask, item);
  usd += answer.usd;
  seconds += answer.seconds;
  if (wrong.length === 0) passed += 1;
  console.log(`${wrong.length === 0 ? "ok  " : "FAIL"} ${item.id.padEnd(16)} ${wrong.join("; ")}`);
}
console.log(`\n${passed}/${cases.length} questions produced the expected filters · ${useApi ? `API, ${MODEL}` : "Claude Code CLI, haiku"} · $${usd.toFixed(4)}${useApi ? "" : " (list price, on your plan)"} · ${(seconds / cases.length).toFixed(1)} s each`);
