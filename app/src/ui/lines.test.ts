import { describe, expect, test } from "vitest";

import { splitLine } from "./lines";

describe("splitLine", () => {
  test.each([
    ["2 tazas de agua", { qty: "2", unit: "tazas", name: "agua" }],
    ["905 g de papa", { qty: "905", unit: "g", name: "papa" }],
    ["1 1/2 cdas. de aceite de oliva", { qty: "1 1/2", unit: "cdas.", name: "aceite de oliva" }],
    ["1/2 cdita. de sal de mar", { qty: "1/2", unit: "cdita.", name: "sal de mar" }],
    ["2 tbsps. olive oil", { qty: "2", unit: "tbsps.", name: "olive oil" }],
    ["1 cup of milk", { qty: "1", unit: "cup", name: "milk" }],
    ["3 dientes de ajo", { qty: "3", unit: "dientes", name: "ajo" }],
    ["2 to 3 cups", { qty: "2", unit: "", name: "to 3 cups" }],
    ["2-3 tazas de caldo", { qty: "2-3", unit: "tazas", name: "caldo" }],
    ["½ taza de leche", { qty: "½", unit: "taza", name: "leche" }],
  ])("%s", (line, expected) => {
    expect(splitLine(line)).toEqual(expected);
  });

  test("a count with no unit keeps the whole noun as the name", () => {
    expect(splitLine("2 huevos")).toEqual({ qty: "2", unit: "", name: "huevos" });
    expect(splitLine("4 papas russet, bien lavadas")).toEqual({ qty: "4", unit: "", name: "papas russet, bien lavadas" });
  });

  test("a line with no quantity is all name", () => {
    expect(splitLine("sal y pimienta al gusto")).toEqual({ qty: "", unit: "", name: "sal y pimienta al gusto" });
    expect(splitLine("  aceite  ")).toEqual({ qty: "", unit: "", name: "aceite" });
  });
});
