import { describe, expect, it } from "vitest";

import { askModel } from "./client";
import { cleanQuestion, parseAsk } from "./spec";

const reply = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;

describe("parseAsk", () => {
  it("accepts a well-formed answer and trims it", () => {
    expect(parseAsk({ have: [" pollo "], exclude: [], maxMissing: 1, maxTotalMin: 30, ignored: [] })).toEqual({
      have: ["pollo"], exclude: [], maxMissing: 1, maxTotalMin: 30, ignored: [],
    });
  });
  it("clamps limits and drops nonsense times", () => {
    expect(parseAsk({ have: [], exclude: [], maxMissing: 99, maxTotalMin: -5, ignored: [] })).toMatchObject({ maxMissing: 3 });
    expect(parseAsk({ have: [], exclude: [], maxMissing: 2, maxTotalMin: -5, ignored: [] })?.maxTotalMin).toBeUndefined();
  });
  it("rejects wrong shapes", () => {
    expect(parseAsk(null)).toBeNull();
    expect(parseAsk({ have: "pollo", exclude: [], maxMissing: 0 })).toBeNull();
    expect(parseAsk({ have: [1], exclude: [], maxMissing: 0 })).toBeNull();
    expect(parseAsk({ have: Array(50).fill("x"), exclude: [], maxMissing: 0 })).toBeNull();
  });
});

describe("cleanQuestion", () => {
  it("refuses empty and long questions", () => {
    expect(cleanQuestion("  ")).toBeNull();
    expect(cleanQuestion("x".repeat(301))).toBeNull();
    expect(cleanQuestion(" hola ")).toBe("hola");
  });
});

describe("askModel", () => {
  const ask = { have: ["pollo"], exclude: [], maxMissing: 0, ignored: [] };
  it("returns the filters", async () => {
    expect(await askModel("pollo", "/api/ask", reply(200, { ask }))).toEqual({ ok: true, ask });
  });
  it("says why when the proxy refuses", async () => {
    const outcome = await askModel("pollo", "/api/ask", reply(429, { error: "rate" }));
    expect(outcome).toMatchObject({ ok: false, message: expect.stringContaining("hora") });
  });
  it("treats a malformed answer as not understood", async () => {
    expect(await askModel("pollo", "/api/ask", reply(200, { ask: { have: 3 } }))).toMatchObject({ ok: false });
  });
  it("explains being offline", async () => {
    const offline = (async () => { throw new TypeError("network"); }) as typeof fetch;
    expect(await askModel("pollo", "/api/ask", offline)).toMatchObject({ ok: false, message: expect.stringContaining("Sin conexión") });
  });
  it("does not call out without a URL or a question", async () => {
    let calls = 0;
    const spy = (async () => { calls += 1; return new Response("{}"); }) as typeof fetch;
    await askModel("pollo", null, spy);
    await askModel("", "/api/ask", spy);
    expect(calls).toBe(0);
  });
});
