// Contract tests: the loader and the core against contract.db, built by the Python ingest from the
// fixture recipes (see test/contract-setup.ts). Each test works on its own copy of the file.
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { beforeEach, describe, expect, inject, test } from "vitest";

import { match } from "../core/match";
import { search } from "../core/search";
import type { RecipeForSearch, SearchData } from "../core/types";
import { type Database, loadSearchData, type Row } from "./load";

let sqlite: DatabaseSync;
const db: Database = { all: (sql) => sqlite.prepare(sql).all() as Row[] };

beforeEach(() => {
  const path = join(mkdtempSync(join(tmpdir(), "recetario-test-")), "contract.db");
  copyFileSync(inject("contractDb"), path);
  sqlite = new DatabaseSync(path);
  return () => sqlite.close();
});

const entry = (name: string): number => {
  const row = sqlite.prepare("SELECT id FROM vocab_entries WHERE name_en = ?").get(name);
  if (row === undefined) throw new Error(`no entry ${name}`);
  return Number(row["id"]);
};
const recipeId = (key: string): number => {
  const row = sqlite.prepare("SELECT id FROM recipes WHERE locator = ?").get(`fixture:${key}`);
  if (row === undefined) throw new Error(`no recipe ${key}`);
  return Number(row["id"]);
};
const recipeKey = (id: number): string => {
  const row = sqlite.prepare("SELECT locator FROM recipes WHERE id = ?").get(id);
  return String(row?.["locator"]).replace("fixture:", "");
};
const find = (data: SearchData, key: string): RecipeForSearch => {
  const found = data.recipes.find((r) => r.id === recipeId(key));
  if (found === undefined) throw new Error(`recipe ${key} not loaded`);
  return found;
};

/** My kitchen: everything obtainable except walnuts; I own a blender and an oven. */
function setUpKitchen() {
  sqlite.exec(`
    UPDATE ingredient_info SET availability = 'obtainable';
    UPDATE ingredient_info SET availability = 'unobtainable'
      WHERE entry_id = (SELECT id FROM vocab_entries WHERE kind = 'ingredient' AND name_en = 'walnut');
    UPDATE equipment_info SET owned = 1
      WHERE entry_id IN (SELECT id FROM vocab_entries WHERE kind = 'equipment' AND name_en IN ('blender', 'oven'));
  `);
}

describe("loadSearchData", () => {
  test("loads every fixture recipe", () => {
    expect(loadSearchData(db).recipes).toHaveLength(10);
  });

  test("groups alternatives and drops staples, basics and optional lines", () => {
    const data = loadSearchData(db);
    expect(find(data, "ejotes-salteados").requirements).toEqual([
      { kind: "ingredient", anyOf: [entry("green beans")] },
      { kind: "ingredient", anyOf: [entry("garlic")] },
    ]);
    expect(find(data, "papas-crujientes").requirements).toContainEqual({
      kind: "equipment",
      anyOf: [entry("air fryer"), entry("oven")],
    });
  });

  test("keeps sub-recipes as a reference and knows what a recipe produces", () => {
    const data = loadSearchData(db);
    expect(find(data, "pasta-al-pesto").requirements).toEqual([
      { kind: "ingredient", anyOf: [entry("pasta")] },
      { kind: "recipe", recipeId: recipeId("pesto") },
    ]);
    expect(find(data, "pesto").produces).toBe(entry("pesto"));
  });

  test("an unresolved line is a null alternative", () => {
    expect(find(loadSearchData(db), "pepino-con-chamoy").requirements).toContainEqual({
      kind: "ingredient",
      anyOf: [null],
    });
  });

  test("reads availability, ownership and the hierarchy", () => {
    setUpKitchen();
    const { catalog } = loadSearchData(db);
    expect(catalog.availability.get(entry("walnut"))).toBe("unobtainable");
    expect(catalog.owned).toEqual(new Set([entry("blender"), entry("oven")]));
    expect(catalog.parentOf.get(entry("cheddar cheese"))).toBe(entry("cheese"));
  });

  test("a merged entry stands for the entry it was merged into", () => {
    sqlite.prepare("UPDATE vocab_entries SET merged_into_id = ? WHERE id = ?").run(entry("walnut"), entry("pecan"));
    expect(find(loadSearchData(db), "pesto").requirements).toContainEqual({
      kind: "ingredient",
      anyOf: [entry("walnut")],
    });
  });

  test("refuses a database with another schema version", () => {
    sqlite.exec("PRAGMA user_version = 1");
    expect(() => loadSearchData(db)).toThrow(/schema version 1/);
  });
});

describe("search over the fixtures", () => {
  const onHand = () =>
    ["egg", "tomato", "onion", "serrano pepper", "avocado", "lime", "pasta", "basil", "cucumber", "banana", "milk"].map(entry);

  test("a fresh database knows nothing yet: only recipes fully on hand match", () => {
    const data = loadSearchData(db);
    expect(search({ onHand: [], maxMissing: 10 }, data)).toEqual([]);
    expect(search({ onHand: onHand(), maxMissing: 10 }, data).map((r) => recipeKey(r.recipeId))).toEqual([
      "guacamole", "huevos-a-la-mexicana",
    ]);
  });

  test("my kitchen", () => {
    setUpKitchen();
    const results = search({ onHand: onHand(), maxMissing: 1 }, loadSearchData(db));
    expect(results.map((r) => recipeKey(r.recipeId))).toEqual([
      "guacamole", "huevos-a-la-mexicana", "licuado-de-platano", "pasta-al-pesto",
    ]);
    // Making pesto needs pecans, parmesan and garlic; buying pesto is one item.
    expect(results.at(-1)?.missing).toEqual([{ anyOf: [entry("pesto")] }]);
  });

  test("pesto on hand completes pasta al pesto", () => {
    setUpKitchen();
    const results = search({ onHand: [...onHand(), entry("pesto")], maxMissing: 0 }, loadSearchData(db));
    expect(results.map((r) => recipeKey(r.recipeId))).toContain("pasta-al-pesto");
  });

  test("cheddar on hand covers a recipe asking for cheese", () => {
    setUpKitchen();
    const data = loadSearchData(db);
    const recipes = new Map(data.recipes.map((r) => [r.id, r]));
    const result = match(find(data, "quesadillas"), {
      catalog: data.catalog,
      recipes,
      onHand: [entry("corn tortilla"), entry("cheddar cheese")],
    });
    expect(result).toEqual({ kind: "match", missing: [] });
  });

  test("an unresolved line blocks its recipe until it's reviewed", () => {
    setUpKitchen();
    const data = loadSearchData(db);
    const result = match(find(data, "pepino-con-chamoy"), {
      catalog: data.catalog,
      recipes: new Map(data.recipes.map((r) => [r.id, r])),
      onHand: onHand(),
    });
    expect(result).toEqual({ kind: "blocked", blocks: [{ reason: "unresolved", ids: [recipeId("pepino-con-chamoy")] }] });
  });
});
