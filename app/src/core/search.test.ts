import { describe, expect, test } from "vitest";

import { questionsToAsk, search } from "./search";
import type { Catalog, RecipeForSearch, Requirement, SearchData, SearchFilters } from "./types";

const EGG = 1, TOMATO = 2, ONION = 3, SAFFRON = 4, RICE = 5;

const catalog: Catalog = {
  parentOf: new Map(),
  availability: new Map([
    [EGG, "obtainable"], [TOMATO, "obtainable"], [ONION, "obtainable"], [RICE, "obtainable"], [SAFFRON, "unobtainable"],
  ]),
  owned: new Set(),
};

const ing = (...anyOf: number[]): Requirement => ({ kind: "ingredient", anyOf });
const recipe = (
  id: number,
  requirements: Requirement[],
  extra: Partial<Omit<RecipeForSearch, "id" | "requirements">> = {},
): RecipeForSearch => ({ id, requirements, produces: null, totalMin: null, rating: null, duplicateOf: null, ...extra });

const data: SearchData = {
  catalog,
  recipes: [
    recipe(10, [ing(EGG), ing(TOMATO)], { totalMin: 15 }),
    recipe(11, [ing(EGG), ing(ONION)], { totalMin: 10 }),
    recipe(12, [ing(EGG)], { totalMin: 30, rating: 3 }),
    recipe(13, [ing(EGG)], { totalMin: 30, rating: 5 }),
    recipe(14, [ing(EGG)], { totalMin: null }),
    recipe(15, [ing(EGG)], { totalMin: 5, duplicateOf: 12 }),
    recipe(16, [ing(RICE), ing(SAFFRON)], { totalMin: 5 }),
    recipe(17, [ing(TOMATO), ing(ONION), ing(RICE)], { totalMin: 5 }),
  ],
};

const filters = (extra: Partial<SearchFilters> = {}): SearchFilters => ({ onHand: [EGG], maxMissing: 1, ...extra });
const ids = (f: SearchFilters) => search(f, data).map((r) => r.recipeId);

describe("search", () => {
  test("ranks by missing, then time (unknown last), then rating; skips duplicates and blocked", () => {
    expect(ids(filters())).toEqual([13, 12, 14, 11, 10]);
  });

  test("maxMissing 0 keeps only full matches", () => {
    expect(ids(filters({ maxMissing: 0 }))).toEqual([13, 12, 14]);
  });

  test("maxTotalMin drops slower recipes and those without a time", () => {
    expect(ids(filters({ maxTotalMin: 15 }))).toEqual([11, 10]);
  });

  test("excluded ingredients drop recipes", () => {
    expect(ids(filters({ excludeIngredients: [ONION] }))).toEqual([13, 12, 14, 10]);
  });

  test("results carry what's missing", () => {
    expect(search(filters({ maxMissing: 3 }), data).find((r) => r.recipeId === 17)?.missing).toEqual([
      { anyOf: [TOMATO] }, { anyOf: [ONION] }, { anyOf: [RICE] },
    ]);
  });
});

describe("questionsToAsk", () => {
  test("asks first about what completes the most recipes", () => {
    const results = search({ onHand: [EGG], maxMissing: 3 }, data);
    expect(questionsToAsk(results)).toEqual([
      { entryId: TOMATO, completes: 1, missingIn: 2 },
      { entryId: ONION, completes: 1, missingIn: 2 },
      { entryId: RICE, completes: 0, missingIn: 1 },
    ]);
  });

  test("respects the limit", () => {
    expect(questionsToAsk(search({ onHand: [], maxMissing: 3 }, data), 1)).toEqual([
      { entryId: EGG, completes: 3, missingIn: 5 },
    ]);
  });
});
