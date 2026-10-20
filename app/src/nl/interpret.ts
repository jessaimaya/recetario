// Server side only (the proxy function and the eval): one question in, filters out. The app never imports this file.

import type Anthropic from "@anthropic-ai/sdk";

import { ASK_SYSTEM, ASK_TOOL, parseAsk, type Ask } from "./spec";

export const MODEL = "claude-haiku-5-5";
/** Dollars per million tokens for MODEL, up to 100K input tokens. */
export const PRICE = { input: 0.1, output: 0.5 };

export interface Interpreted {
  /** null when the model did not call the tool or called it with something off-shape. */
  ask: Ask | null;
  inputTokens: number;
  outputTokens: number;
}

export const costUsd = (inputTokens: number, outputTokens: number): number =>
  (inputTokens * PRICE.input + outputTokens * PRICE.output) / 1_000_000;

export async function interpret(client: Anthropic, question: string, model = MODEL): Promise<Interpreted> {
  const response = await client.messages.create({
    model,
    max_tokens: 500,
    thinking: { type: "disabled" },
    system: ASK_SYSTEM,
    tools: [ASK_TOOL],
    messages: [{ role: "user", content: question }],
  });
  const call = response.content.find((block) => block.type === "tool_use" && block.name === ASK_TOOL.name);
  return {
    ask: call?.type === "tool_use" ? parseAsk(call.input) : null,
    inputTokens: response.usage.input_tokens,
    outputTokens: response.usage.output_tokens,
  };
}
