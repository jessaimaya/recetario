import { describe, expect, test } from "vitest";

import type { Entry } from "../db/queries";
import { findEntries, resolveTerm, splitTerms } from "./find";

const entry = (id: number, nameEn: string, label: string, aliases: string[] = [], isStaple = false): Entry => ({
  id, kind: "ingredient", nameEn, label, parentId: null, isStaple, isBasic: false, availability: "unknown", owned: false, aliases,
});

const entries = [
  entry(1, "tomato", "jitomate", ["tomatoes"]),
  entry(2, "tomatillo", "tomate verde"),
  entry(3, "corn tortilla", "tortilla de maíz", ["corn tortillas"]),
  entry(4, "green onion", "cebolla cambray", ["scallion"]),
  entry(5, "onion", "cebolla"),
  entry(6, "salt", "sal", ["sea salt"], true),
  entry(7, "egg", "huevo"),
];

describe("splitTerms", () => {
  test("splits on commas, semicolons, new lines and 'y'", () => {
    expect(splitTerms("brócoli, tortillas")).toEqual(["brócoli", "tortillas"]);
    expect(splitTerms("huevo y jitomate; cebolla\nsal")).toEqual(["huevo", "jitomate", "cebolla", "sal"]);
    expect(splitTerms("  , ,  ")).toEqual([]);
  });

  test("keeps a name that merely contains a 'y' inside a word", () => {
    expect(splitTerms("yuca, yogur")).toEqual(["yuca", "yogur"]);
  });
});

describe("resolveTerm", () => {
  test.each([
    ["tortillas", "tortilla de maíz"],
    ["Jitomates", "jitomate"],
    ["huevos", "huevo"],
    ["scallion", "cebolla cambray"],
    ["cebolla", "cebolla"],
    ["cebollas", "cebolla"],
    ["tomate verde", "tomate verde"],
    ["corn tortillas", "tortilla de maíz"],
  ])("%s", (typed, label) => {
    expect(resolveTerm(typed, entries)?.label).toBe(label);
  });

  test("accents and case don't matter", () => {
    expect(resolveTerm("TORTILLA DE MAIZ", entries)?.label).toBe("tortilla de maíz");
  });

  test("something that isn't in the vocabulary finds nothing", () => {
    expect(resolveTerm("brócoli", entries)).toBeNull();
    expect(resolveTerm("", entries)).toBeNull();
  });
});

describe("findEntries", () => {
  test("ranks exact before prefix before contains, and caps the list", () => {
    expect(findEntries("cebolla", entries).map((e) => e.label)).toEqual(["cebolla", "cebolla cambray"]);
    expect(findEntries("o", entries, 2).length).toBeLessThanOrEqual(2);
  });
});
