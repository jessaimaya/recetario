import { describe, expect, test } from "vitest";

import { match } from "./match";
import type { Availability, Catalog, MatchResult, RecipeForMatch, Requirement } from "./types";

// A small synthetic catalog. Staples, basic equipment and optional lines never reach the core
// (the recipe_requirements view drops them), so they are covered by the contract tests instead.
const CHEESE = 1, CHEDDAR = 2, TOMATO = 3, WALNUT = 4, PECAN = 5, BASIL = 6, PESTO = 7, SAFFRON = 8,
  KALE = 9, GARLIC = 10, LEMONGRASS = 11;
const OVEN = 50, AIR_FRYER = 51, BLENDER = 52, FOOD_PROCESSOR = 53, PRESSURE_COOKER = 54, INSTANT_POT = 55;

const availability: [number, Availability][] = [
  [CHEDDAR, "obtainable"], [TOMATO, "obtainable"], [WALNUT, "unobtainable"], [PECAN, "obtainable"],
  [BASIL, "obtainable"], [PESTO, "obtainable"], [SAFFRON, "unobtainable"], [GARLIC, "obtainable"],
  [LEMONGRASS, "unobtainable"],
  // CHEESE has no availability of its own (it's covered by CHEDDAR); KALE is unknown.
];

const catalog: Catalog = {
  parentOf: new Map([[CHEDDAR, CHEESE], [INSTANT_POT, PRESSURE_COOKER]]),
  availability: new Map(availability),
  owned: new Set([OVEN, FOOD_PROCESSOR, INSTANT_POT]),
};

const ing = (...anyOf: (number | null)[]): Requirement => ({ kind: "ingredient", anyOf });
const eq = (...anyOf: number[]): Requirement => ({ kind: "equipment", anyOf });
const sub = (recipeId: number): Requirement => ({ kind: "recipe", recipeId });
const recipe = (id: number, produces: number | null, ...requirements: Requirement[]): RecipeForMatch => ({
  id, produces, requirements,
});

const PESTO_RECIPE = 100, PLAIN_SUB = 101, HARD_PESTO = 102, CYCLE_A = 103, CYCLE_B = 104, KALE_CHIPS = 105;
const recipes = new Map(
  [
    recipe(PESTO_RECIPE, PESTO, ing(BASIL), ing(WALNUT, PECAN), eq(BLENDER, FOOD_PROCESSOR)),
    recipe(PLAIN_SUB, null, ing(BASIL)),
    recipe(HARD_PESTO, PESTO, ing(SAFFRON)),
    recipe(CYCLE_A, null, sub(CYCLE_B)),
    recipe(CYCLE_B, null, sub(CYCLE_A)),
    recipe(KALE_CHIPS, KALE, ing(LEMONGRASS)),
  ].map((r) => [r.id, r]),
);

const ok = (...missing: number[][]): MatchResult => ({ kind: "match", missing: missing.map((anyOf) => ({ anyOf })) });
const unresolvedNeed = (label = "") => ({ anyOf: [], unresolved: label });
const blocked = (...blocks: [string, number[]][]): MatchResult => ({
  kind: "blocked",
  blocks: blocks.map(([reason, ids]) => ({ reason, ids })) as Extract<MatchResult, { kind: "blocked" }>["blocks"],
});

const UNDER_TEST = 1;

interface Case {
  name: string;
  needs: Requirement[];
  onHand?: number[];
  onHandText?: string[];
  exclude?: number[];
  expected: MatchResult;
}

const cases: Case[] = [
  // Plain ingredients
  { name: "no requirements", needs: [], expected: ok() },
  { name: "empty on-hand lists every requirement as missing", needs: [ing(TOMATO), ing(GARLIC)], expected: ok([TOMATO], [GARLIC]) },
  { name: "on hand covers a requirement", needs: [ing(TOMATO), ing(GARLIC)], onHand: [TOMATO], expected: ok([GARLIC]) },
  { name: "unknown availability blocks as unclassified", needs: [ing(KALE)], expected: blocked(["unclassified", [KALE]]) },
  { name: "unobtainable blocks", needs: [ing(SAFFRON)], expected: blocked(["unobtainable", [SAFFRON]]) },
  { name: "on hand beats unobtainable", needs: [ing(SAFFRON)], onHand: [SAFFRON], expected: ok() },
  { name: "on hand beats unknown", needs: [ing(KALE)], onHand: [KALE], expected: ok() },
  { name: "an unresolved line counts as one missing ingredient, not a block", needs: [ing(null)], expected: { kind: "match", missing: [unresolvedNeed()] } },
  { name: "an unresolved line is covered by the person's words", needs: [{ kind: "ingredient", anyOf: [null], label: "2 cups chopped kale" }], onHandText: ["kale"], expected: ok() },
  { name: "words match whole words only", needs: [{ kind: "ingredient", anyOf: [null], label: "kalematic" }], onHandText: ["kale"], expected: { kind: "match", missing: [unresolvedNeed("kalematic")] } },
  { name: "plural words cover too", needs: [{ kind: "ingredient", anyOf: [null], label: "3 tomatoes" }], onHandText: ["tomato"], expected: ok() },
  { name: "the same unresolved line is listed once", needs: [{ kind: "ingredient", anyOf: [null], label: "saffron" }, { kind: "ingredient", anyOf: [null], label: "saffron" }], expected: { kind: "match", missing: [unresolvedNeed("saffron")] } },

  // Hierarchy
  { name: "a child on hand covers its parent", needs: [ing(CHEESE)], onHand: [CHEDDAR], expected: ok() },
  { name: "a parent on hand doesn't cover a child", needs: [ing(CHEDDAR)], onHand: [CHEESE], expected: ok([CHEDDAR]) },
  { name: "a parent is obtainable when a child is", needs: [ing(CHEESE)], expected: ok([CHEESE]) },

  // OR-groups
  { name: "OR-group: one alternative on hand", needs: [ing(WALNUT, PECAN)], onHand: [WALNUT], expected: ok() },
  { name: "OR-group: only obtainable alternatives are suggested", needs: [ing(WALNUT, PECAN)], expected: ok([PECAN]) },
  { name: "OR-group: all unobtainable", needs: [ing(WALNUT, SAFFRON)], expected: blocked(["unobtainable", [WALNUT, SAFFRON]]) },
  { name: "OR-group: unknown + unobtainable is unclassified", needs: [ing(WALNUT, KALE)], expected: blocked(["unclassified", [KALE]]) },
  { name: "OR-group: an unresolved alternative doesn't block an obtainable one", needs: [ing(null, PECAN)], expected: ok([PECAN]) },
  { name: "OR-group: unresolved + unobtainable is an unresolved need", needs: [ing(null, WALNUT)], expected: { kind: "match", missing: [unresolvedNeed()] } },

  // Exclusions
  { name: "excluded ingredient blocks", needs: [ing(TOMATO)], exclude: [TOMATO], expected: blocked(["excluded", [TOMATO]]) },
  { name: "excluding a parent excludes its children", needs: [ing(CHEDDAR)], exclude: [CHEESE], expected: blocked(["excluded", [CHEDDAR]]) },
  { name: "excluding a child keeps the generic parent", needs: [ing(CHEESE)], exclude: [CHEDDAR], expected: ok([CHEESE]) },
  { name: "excluded alternative leaves the other", needs: [ing(PECAN, TOMATO)], exclude: [PECAN], expected: ok([TOMATO]) },
  { name: "excluded wins over on hand", needs: [ing(TOMATO)], onHand: [TOMATO], exclude: [TOMATO], expected: blocked(["excluded", [TOMATO]]) },

  // Equipment
  { name: "owned equipment", needs: [eq(OVEN)], expected: ok() },
  { name: "equipment not owned blocks", needs: [eq(AIR_FRYER)], expected: blocked(["equipment", [AIR_FRYER]]) },
  { name: "equipment OR-group: one owned", needs: [eq(AIR_FRYER, OVEN)], expected: ok() },
  { name: "equipment OR-group: none owned", needs: [eq(AIR_FRYER, BLENDER)], expected: blocked(["equipment", [AIR_FRYER, BLENDER]]) },
  { name: "owning a child covers its parent", needs: [eq(PRESSURE_COOKER)], expected: ok() },
  { name: "equipment never adds to missing", needs: [eq(OVEN), ing(TOMATO)], expected: ok([TOMATO]) },

  // Sub-recipes
  { name: "sub-recipe: having its product covers it", needs: [sub(PESTO_RECIPE)], onHand: [PESTO], expected: ok() },
  { name: "sub-recipe: making it from what's on hand", needs: [sub(PESTO_RECIPE)], onHand: [BASIL, PECAN], expected: ok() },
  { name: "sub-recipe: one ingredient short, making it wins the tie", needs: [sub(PESTO_RECIPE)], onHand: [BASIL], expected: ok([PECAN]) },
  { name: "sub-recipe: buying the product beats buying two ingredients", needs: [sub(PESTO_RECIPE)], expected: ok([PESTO]) },
  { name: "sub-recipe: can't be made, so buy the product", needs: [sub(HARD_PESTO)], expected: ok([PESTO]) },
  { name: "sub-recipe: product excluded, made instead", needs: [sub(PESTO_RECIPE)], onHand: [BASIL, PECAN], exclude: [PESTO], expected: ok() },
  { name: "sub-recipe: product excluded and can't be made", needs: [sub(HARD_PESTO)], exclude: [PESTO], expected: blocked(["unobtainable", [SAFFRON]]) },
  { name: "sub-recipe: unknown product doesn't rescue it", needs: [sub(KALE_CHIPS)], expected: blocked(["unobtainable", [LEMONGRASS]]) },
  { name: "sub-recipe: no product, its needs count", needs: [sub(PLAIN_SUB)], expected: ok([BASIL]) },
  { name: "sub-recipe: needs shared with the parent are listed once", needs: [ing(BASIL), sub(PLAIN_SUB)], expected: ok([BASIL]) },
  { name: "sub-recipe: missing recipe is unresolved", needs: [sub(999)], expected: blocked(["unresolved", [UNDER_TEST]]) },
  { name: "sub-recipe: a cycle is unresolved", needs: [sub(CYCLE_A)], expected: blocked(["unresolved", [CYCLE_B]]) },

  // Several blocks at once
  {
    name: "blocks are merged and ordered by reason",
    needs: [ing(KALE), ing(SAFFRON), eq(AIR_FRYER), ing(null), ing(LEMONGRASS)],
    expected: blocked(["unobtainable", [SAFFRON, LEMONGRASS]], ["equipment", [AIR_FRYER]], ["unclassified", [KALE]]),
  },
];

describe("match", () => {
  test.each(cases)("$name", ({ needs, onHand = [], onHandText, exclude, expected }) => {
    const result = match(recipe(UNDER_TEST, null, ...needs), { catalog, recipes, onHand, onHandText, exclude });
    expect(result).toEqual(expected);
  });
});
