import { describe, expect, test } from "vitest";

import { blockedCounts, classificationQuestions, questionsToAsk, search } from "./search";
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


describe("classificationQuestions", () => {
  const MISO = 8, SUMAC = 9;
  const unknownData: SearchData = {
    catalog,
    recipes: [
      recipe(20, [ing(EGG), ing(MISO)]),
      recipe(21, [ing(EGG), ing(MISO)]),
      recipe(22, [ing(EGG), ing(MISO), ing(SUMAC)]),
      recipe(23, [ing(EGG), ing(SUMAC)]),
      recipe(24, [ing(EGG), ing(SAFFRON)]),
      recipe(25, [ing(EGG), ing(MISO)], { duplicateOf: 20 }),
      recipe(26, [ing(EGG)]),
    ],
  };

  test("asks first about the ingredient that unlocks the most recipes", () => {
    expect(classificationQuestions(filters(), unknownData)).toEqual([
      { entryId: MISO, unlocks: 2, appearsIn: 3 },
      { entryId: SUMAC, unlocks: 1, appearsIn: 2 },
    ]);
  });

  test("an ingredient on hand needs no question, and blocked-for-other-reasons recipes don't count", () => {
    const questions = classificationQuestions(filters({ onHand: [EGG, MISO] }), unknownData);
    expect(questions.map((q) => q.entryId)).toEqual([SUMAC]);
  });

  test("blockedCounts counts each recipe once under its first reason and skips duplicates", () => {
    expect(blockedCounts(filters(), unknownData)).toEqual({ unclassified: 4, unobtainable: 1 });
  });
});
