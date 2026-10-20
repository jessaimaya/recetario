/** Splits an ingredient line into the three parts the recipe page sets apart: quantity, unit and name. */
export interface SplitLine {
  qty: string;
  unit: string;
  name: string;
}

const UNITS = new Set([
  // es-MX
  "taza", "tazas", "cucharada", "cucharadas", "cda", "cdas", "cucharadita", "cucharaditas", "cdita", "cditas", "pizca", "pizcas",
  "diente", "dientes", "lata", "latas", "paquete", "paquetes", "rebanada", "rebanadas", "cabeza", "cabezas", "manojo", "manojos",
  "ramita", "ramitas", "tallo", "tallos", "hoja", "hojas", "puñado", "puñados", "barra", "barras", "bolsa", "bolsas", "frasco", "frascos",
  "botella", "botellas", "pieza", "piezas", "gotas", "gota", "chorrito", "g", "kg", "mg", "ml", "l", "litro", "litros", "gramos", "oz", "lb",
  // en
  "cup", "cups", "tbsp", "tbsps", "tsp", "tsps", "clove", "cloves", "can", "cans", "package", "packages", "slice", "slices", "pinch",
  "pinches", "dash", "dashes", "head", "heads", "bunch", "bunches", "stalk", "stalks", "sprig", "sprigs", "stick", "sticks", "jar", "jars",
  "bottle", "bottles", "bag", "bags", "box", "boxes", "handful", "quart", "quarts", "pint", "pints", "gallon", "gallons", "ounce", "ounces",
  "pound", "pounds", "lbs", "fillet", "fillets", "ear", "ears",
]);

const QUANTITY = /^(\d+\s+\d+\/\d+|\d+\/\d+|\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?|[½⅓⅔¼¾⅛])\s+/;

export function splitLine(text: string): SplitLine {
  const trimmed = text.trim();
  const quantity = QUANTITY.exec(trimmed);
  if (quantity === null) return { qty: "", unit: "", name: trimmed };

  let rest = trimmed.slice(quantity[0].length);
  let unit = "";
  const [word = ""] = rest.split(/\s+/, 1);
  if (UNITS.has(word.toLowerCase().replace(/\.$/, ""))) {
    unit = word;
    rest = rest.slice(word.length).trimStart();
    rest = rest.replace(/^(?:de|del|of)\s+/i, "");
  }
  return { qty: quantity[1]!.replace(/\s+/g, " "), unit, name: rest };
}
