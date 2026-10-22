import type { Availability, EntryId, RecipeForSearch, Requirement, SearchData } from "../core/types";
import { LOCALE } from "./queries";

/** The schema version this app reads (PRAGMA user_version, set by the Python migrations). */
export const SCHEMA_VERSION = 6; // 3 only added duplicate_reviews and 4 recipe_lines.variant, neither read by the app; 5 added substitute_group; 6 recipes.course

export type Row = Record<string, unknown>;

/** The one thing the loader needs from a SQLite driver: node:sqlite in tests, SQLite WASM in the browser. */
export interface Database {
  all(sql: string, bind?: unknown[]): Row[];
}

export function loadSearchData(db: Database): SearchData {
  const version = Number(db.all("PRAGMA user_version")[0]?.["user_version"]);
  if (version !== SCHEMA_VERSION) {
    throw new Error(`database schema version ${version}, this app reads version ${SCHEMA_VERSION}`);
  }

  // A merged entry stands for the entry it was merged into.
  const mergedInto = new Map<EntryId, EntryId>();
  const parentOf = new Map<EntryId, EntryId>();
  const substituteGroupOf = new Map<EntryId, string>();
  for (const row of db.all("SELECT id, parent_id, merged_into_id, substitute_group FROM vocab_entries")) {
    if (row["merged_into_id"] !== null) mergedInto.set(num(row["id"]), num(row["merged_into_id"]));
    else if (row["substitute_group"] !== null) substituteGroupOf.set(num(row["id"]), String(row["substitute_group"]));
    if (row["parent_id"] !== null) parentOf.set(num(row["id"]), num(row["parent_id"]));
  }
  const canonical = (id: EntryId): EntryId => {
    const seen = new Set<EntryId>();
    while (mergedInto.has(id) && !seen.has(id)) {
      seen.add(id);
      id = mergedInto.get(id)!;
    }
    return id;
  };
  const canonicalOrNull = (value: unknown) => (value === null ? null : canonical(num(value)));

  const availability = new Map<EntryId, Availability>();
  for (const row of db.all("SELECT entry_id, availability FROM ingredient_info WHERE availability != 'unknown'")) {
    availability.set(num(row["entry_id"]), row["availability"] as Availability);
  }
  const owned = new Set(db.all("SELECT entry_id FROM equipment_info WHERE owned = 1").map((row) => num(row["entry_id"])));

  const recipes = new Map<number, RecipeForSearch>();
  const recipeRows = db.all(`
    SELECT r.id, r.produces_id, r.total_min, r.duplicate_of, r.course,
           (SELECT avg(rating) FROM cook_log AS c WHERE c.recipe_id = r.id) AS rating
    FROM recipes AS r ORDER BY r.id
  `);
  for (const row of recipeRows) {
    recipes.set(num(row["id"]), {
      id: num(row["id"]),
      produces: canonicalOrNull(row["produces_id"]),
      requirements: [],
      totalMin: row["total_min"] === null ? null : num(row["total_min"]),
      rating: row["rating"] === null ? null : num(row["rating"]),
      duplicateOf: row["duplicate_of"] === null ? null : num(row["duplicate_of"]),
      course: row["course"] === null ? null : String(row["course"]),
    });
  }

  // Lines the vocabulary hasn't resolved: the view has no text for them, so find it by the key it builds from the line or its OR-group.
  // The label is what the person reads (the translation when there is one); `search` holds both languages for matching.
  const labels = new Map<string, { label: string; search: string }>();
  const openLines = db.all(
    `SELECT r.recipe_id, rl.id, rl.alt_group, r.text_original, t.text AS translated
     FROM recipe_lines AS rl
     JOIN raw_lines AS r ON r.id = rl.raw_line_id
     LEFT JOIN localized_texts AS t ON t.owner_kind = 'raw_line' AND t.owner_id = r.id AND t.locale = ?
     WHERE rl.ingredient_id IS NULL AND rl.sub_recipe_id IS NULL AND rl.is_optional = 0`,
    [LOCALE],
  );
  for (const row of openLines) {
    const key = `${num(row["recipe_id"])}:i:${row["alt_group"] === null ? `l${num(row["id"])}` : `g${num(row["alt_group"])}`}`;
    if (labels.has(key)) continue;
    const original = String(row["text_original"]);
    const translated = row["translated"] === null ? null : String(row["translated"]);
    labels.set(key, { label: translated ?? original, search: translated === null ? original : `${original} ${translated}` });
  }

  // Rows sharing (recipe_id, req_key) are the alternatives of one requirement.
  const groups = new Map<string, Requirement>();
  const reqRows = db.all(
    "SELECT recipe_id, kind, req_key, entry_id, sub_recipe_id FROM recipe_requirements ORDER BY recipe_id, req_key",
  );
  for (const row of reqRows) {
    const recipe = recipes.get(num(row["recipe_id"]));
    if (recipe === undefined) continue;
    const key = `${recipe.id}:${String(row["req_key"])}`;
    const kind = row["kind"];
    let req = groups.get(key);
    if (req === undefined) {
      if (kind === "recipe") req = { kind: "recipe", recipeId: num(row["sub_recipe_id"]) };
      else if (kind === "equipment") req = { kind: "equipment", anyOf: [] };
      else if (kind === "ingredient") req = { kind: "ingredient", anyOf: [] };
      else throw new Error(`unknown requirement kind ${String(kind)}`);
      groups.set(key, req);
      recipe.requirements.push(req);
    }
    // After a merge two alternatives can be the same entry; keep one.
    if (req.kind === "ingredient") {
      const id = canonicalOrNull(row["entry_id"]);
      if (!req.anyOf.includes(id)) req.anyOf.push(id);
      const open = labels.get(key);
      if (id === null && open !== undefined) Object.assign(req, open);
    } else if (req.kind === "equipment") {
      const id = canonical(num(row["entry_id"]));
      if (!req.anyOf.includes(id)) req.anyOf.push(id);
    }
  }

  return { catalog: { parentOf, availability, owned, substituteGroupOf }, recipes: [...recipes.values()] };
}

function num(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "bigint") return Number(value);
  throw new TypeError(`expected a number, got ${String(value)}`);
}
