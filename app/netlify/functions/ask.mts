// The proxy: keeps the Anthropic key off the device. One question in, filters out.
// Guards, in order: off switch, origin, input size, per-visitor rate limit, daily spending cap.
// Settings (Netlify environment): ANTHROPIC_API_KEY, ASK_ENABLED ("true" to turn it on), ASK_ORIGIN (the app's origin),
// ASK_PER_HOUR (default 20 questions per visitor), ASK_DAILY_USD (default 1).

import Anthropic from "@anthropic-ai/sdk";
import { getStore } from "@netlify/blobs";
import type { Config, Context } from "@netlify/functions";

import { costUsd, interpret } from "../../src/nl/interpret";
import { cleanQuestion } from "../../src/nl/spec";

const reply = (status: number, body: object) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

const number = (value: string | undefined, fallback: number) => {
  const parsed = Number(value);
  return value !== undefined && Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
};

/** Counters live in a Netlify Blobs store; a function instance has no memory of the last one. */
async function bump(key: string, by: number): Promise<number> {
  const store = getStore("ask-limits");
  const current = Number((await store.get(key)) ?? 0);
  await store.set(key, String(current + by));
  return current + by;
}

async function read(key: string): Promise<number> {
  return Number((await getStore("ask-limits").get(key)) ?? 0);
}

const digest = async (text: string) =>
  [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)))]
    .slice(0, 8)
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");

export default async (request: Request, context: Context): Promise<Response> => {
  if (Netlify.env.get("ASK_ENABLED") !== "true") return reply(503, { error: "off" });
  if (request.method !== "POST") return reply(405, { error: "method" });

  const origin = Netlify.env.get("ASK_ORIGIN");
  if (origin !== undefined && request.headers.get("origin") !== origin) return reply(403, { error: "origin" });

  let question: string | null;
  try {
    question = cleanQuestion(((await request.json()) as { question?: unknown }).question);
  } catch {
    question = null;
  }
  if (question === null) return reply(400, { error: "question" });

  const hour = new Date().toISOString().slice(0, 13);
  const day = hour.slice(0, 10);

  // the daily cap is in micro-dollars so the counter stays an integer
  const spentMicro = await read(`spent:${day}`);
  if (spentMicro / 1_000_000 >= number(Netlify.env.get("ASK_DAILY_USD"), 1)) return reply(429, { error: "daily-cap" });

  // a hashed address, never the address itself
  const visitor = await digest(context.ip);
  const used = await bump(`visitor:${visitor}:${hour}`, 1);
  if (used > number(Netlify.env.get("ASK_PER_HOUR"), 20)) return reply(429, { error: "rate" });

  try {
    const client = new Anthropic({ apiKey: Netlify.env.get("ANTHROPIC_API_KEY") });
    const result = await interpret(client, question);
    await bump(`spent:${day}`, Math.ceil(costUsd(result.inputTokens, result.outputTokens) * 1_000_000));
    return result.ask === null ? reply(422, { error: "unclear" }) : reply(200, { ask: result.ask });
  } catch (error) {
    console.error("ask failed", error instanceof Error ? error.message : error);
    return reply(502, { error: "model" });
  }
};

export const config: Config = { path: "/api/ask", method: ["POST"] };
