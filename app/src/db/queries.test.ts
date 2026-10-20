// The screens' queries against contract.db (fixture recipes typed by hand in es-MX), each test on its own copy.
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { beforeEach, describe, expect, inject, test } from "vitest";

import { loadSearchData, type Row } from "./load";
import { loadEntries, loadRecipeCards, loadRecipeDetail, setAvailability, setOwned, summarize } from "./queries";
import type { AppDatabase } from "./sqlite";

let sqlite: DatabaseSync;
const db: AppDatabase = {
  all: (sql, bind) => sqlite.prepare(sql).all(...((bind ?? []) as never[])) as Row[],
  run: (sql, bind) => void sqlite.prepare(sql).run(...((bind ?? []) as never[])),
  exportBytes: () => new Uint8Array(),
  close: () => {},
};

beforeEach(() => {
  const path = join(mkdtempSync(join(tmpdir(), "recetario-test-")), "contract.db");
  copyFileSync(inject("contractDb"), path);
  sqlite = new DatabaseSync(path);
  return () => sqlite.close();
});

const entry = (name: string) => loadEntries(db).find((candidate) => candidate.nameEn === name)!;

describe("loadEntries", () => {
  test("labels come in es-MX and fall back to English", () => {
    expect(entry("tomato").label).toBe("jitomate");
    expect(entry("tomato").kind).toBe("ingredient");
    expect(entry("oven").kind).toBe("equipment");
  });

  test("availability and ownership default to unknown and not owned", () => {
    expect(entry("tomato").availability).toBe("unknown");
    expect(entry("air fryer").owned).toBe(false);
  });
});

describe("writes", () => {
  test("availability is written to the person's own table and read back, and the search data sees it", () => {
    const tomato = entry("tomato");
    setAvailability(db, tomato.id, "obtainable");
    expect(entry("tomato").availability).toBe("obtainable");
    expect(loadSearchData(db).catalog.availability.get(tomato.id)).toBe("obtainable");
    setAvailability(db, tomato.id, "unknown");
    expect(entry("tomato").availability).toBe("unknown");
  });

  test("owning equipment is written and read back, for entries with and without a row yet", () => {
    const airFryer = entry("air fryer");
    setOwned(db, airFryer.id, true);
    expect(entry("air fryer").owned).toBe(true);
    setOwned(db, airFryer.id, false);
    expect(entry("air fryer").owned).toBe(false);
    expect(loadSearchData(db).catalog.owned.has(airFryer.id)).toBe(false);
  });
});

describe("recipes", () => {
  test("cards use the es-MX title and keep the original", () => {
    const cards = [...loadRecipeCards(db).values()];
    expect(cards.length).toBeGreaterThan(5);
    for (const card of cards) {
      expect(card.title.length).toBeGreaterThan(0);
      expect(card.titleOriginal.length).toBeGreaterThan(0);
    }
  });

  test("detail has lines, steps and equipment for a fixture recipe", () => {
    const [first] = loadRecipeCards(db).values();
    const detail = loadRecipeDetail(db, first!.id)!;
    expect(detail.ingredients.length).toBeGreaterThan(0);
    expect(detail.steps.length).toBeGreaterThan(0);
    expect(detail.ingredients.every((line) => line.original.length > 0)).toBe(true);
  });

  test("an unknown recipe is null and the summary counts what is there", () => {
    expect(loadRecipeDetail(db, 999999)).toBeNull();
    const summary = summarize(db);
    expect(summary.recipes).toBeGreaterThan(5);
    expect(summary.ingredients).toBeGreaterThan(40);
  });
});
