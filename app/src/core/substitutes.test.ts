import { describe, expect, test } from "vitest";

import { match } from "./match";
import type { Availability, Catalog, MatchResult, RecipeForMatch } from "./types";

// Cooking oils stand in for each other; sesame oil is no substitute; the vinegar has no group.
const COCONUT = 1, OLIVE = 2, CANOLA = 3, SESAME = 4, VINEGAR = 5;

const catalog = (availability: [number, Availability][]): Catalog => ({
  parentOf: new Map(),
  availability: new Map(availability),
  owned: new Set(),
  substituteGroupOf: new Map([[COCONUT, "cooking oil"], [OLIVE, "cooking oil"], [CANOLA, "cooking oil"]]),
});

const needs = (...ids: number[]): RecipeForMatch => ({ id: 1, produces: null, requirements: ids.map((id) => ({ kind: "ingredient", anyOf: [id] })) });

const run = (wanted: number[], options: { onHand?: number[]; availability?: [number, Availability][]; substitutes?: boolean; exclude?: number[] }): MatchResult =>
  match(needs(...wanted), {
    catalog: catalog(options.availability ?? []),
    recipes: new Map(),
    onHand: options.onHand ?? [],
    exclude: options.exclude,
    substitutes: options.substitutes,
  });

describe("substitutes", () => {
  test("a member on hand covers a line for another member, and the swap is reported", () => {
    expect(run([COCONUT], { onHand: [OLIVE] })).toEqual({ kind: "match", missing: [], substitutions: [{ wanted: COCONUT, using: OLIVE }] });
  });

  test("the exact ingredient wins over a substitute, so nothing is reported", () => {
    expect(run([COCONUT], { onHand: [COCONUT, OLIVE] })).toEqual({ kind: "match", missing: [] });
  });

  test("an ingredient outside the group is no substitute", () => {
    expect(run([COCONUT], { onHand: [SESAME], availability: [[COCONUT, "unobtainable"], [OLIVE, "unobtainable"], [CANOLA, "unobtainable"]] })).toEqual({
      kind: "blocked",
      blocks: [{ reason: "unobtainable", ids: [COCONUT] }],
    });
    expect(run([VINEGAR], { onHand: [OLIVE], availability: [[VINEGAR, "obtainable"]] })).toEqual({ kind: "match", missing: [{ anyOf: [VINEGAR] }] });
  });

  test("with substitutes off only the exact ingredient counts", () => {
    expect(run([COCONUT], { onHand: [OLIVE], substitutes: false, availability: [[COCONUT, "obtainable"]] })).toEqual({
      kind: "match",
      missing: [{ anyOf: [COCONUT] }],
    });
  });

  test("an excluded substitute is not used", () => {
    expect(run([COCONUT], { onHand: [OLIVE], exclude: [OLIVE], availability: [[COCONUT, "obtainable"]] })).toEqual({
      kind: "match",
      missing: [{ anyOf: [COCONUT] }],
    });
  });

  test("when the ingredient can't be bought here, a substitute that can be bought is missing instead of blocking", () => {
    expect(run([COCONUT], { availability: [[COCONUT, "unobtainable"], [OLIVE, "obtainable"]] })).toEqual({
      kind: "match",
      missing: [{ anyOf: [OLIVE] }],
    });
  });

  test("the ingredient itself comes first when both can be bought", () => {
    expect(run([COCONUT], { availability: [[COCONUT, "obtainable"], [OLIVE, "obtainable"]] })).toEqual({
      kind: "match",
      missing: [{ anyOf: [COCONUT, OLIVE] }],
    });
  });

  test("an unknown ingredient is asked about before its substitutes", () => {
    expect(run([COCONUT], {})).toEqual({ kind: "blocked", blocks: [{ reason: "unclassified", ids: [COCONUT] }] });
    expect(run([COCONUT], { availability: [[COCONUT, "unobtainable"]] })).toEqual({
      kind: "blocked",
      blocks: [{ reason: "unclassified", ids: [OLIVE, CANOLA] }],
    });
  });
});
