import type { Availability, EntryId, RecipeId } from "../core/types";
import { type Database, type Row } from "./load";
import type { AppDatabase } from "./sqlite";

export const LOCALE = "es-MX";

export interface Entry {
  id: EntryId;
  kind: "ingredient" | "equipment";
  nameEn: string;
  /** Display name in es-MX, falling back to the English name. */
  label: string;
  parentId: EntryId | null;
  isStaple: boolean;
  isBasic: boolean;
  availability: Availability;
  owned: boolean;
  /** Other names that mean the same thing (English and Spanish), for matching what a person types. */
  aliases: string[];
}

export interface RecipeCard {
  id: RecipeId;
  title: string;
  titleOriginal: string;
  totalMin: number | null;
  servings: number | null;
  source: string;
  course: string | null;
}

export interface RecipeLine {
  original: string;
  translated: string | null;
  /** The vocabulary entries this line asks for (more than one for "salt and pepper"); empty when unresolved. */
  ingredientIds: EntryId[];
  /** Not yet matched to the vocabulary, so the recipe is kept out of search results. */
  unresolved: boolean;
}

export interface RecipeDetail extends RecipeCard {
  description: string | null;
  descriptionOriginal: string | null;
  prepMin: number | null;
  locator: string | null;
  ingredients: RecipeLine[];
  steps: RecipeLine[];
  equipment: { label: string; listed: boolean }[];
  attributes: { label: string; value: string; unit: string | null }[];
}

export interface DatabaseSummary {
  recipes: number;
  ingredients: number;
  equipment: number;
  sources: number;
}

const text = (value: unknown): string => String(value);
const maybeText = (value: unknown): string | null => (value === null || value === undefined ? null : String(value));
const maybeNumber = (value: unknown): number | null => (value === null || value === undefined ? null : Number(value));

/** Ingredients and equipment with everything the screens show about them. Merged entries are left out. */
export function loadEntries(db: Database): Entry[] {
  const rows = db.all(
    `SELECT e.id, e.kind, e.name_en, e.parent_id, l.name AS label,
            COALESCE(i.is_staple, 0) AS is_staple, COALESCE(i.availability, 'unknown') AS availability,
            COALESCE(q.owned, 0) AS owned, COALESCE(q.is_basic, 0) AS is_basic
     FROM vocab_entries AS e
     LEFT JOIN vocab_labels AS l ON l.entry_id = e.id AND l.locale = ?
     LEFT JOIN ingredient_info AS i ON i.entry_id = e.id
     LEFT JOIN equipment_info AS q ON q.entry_id = e.id
     WHERE e.kind IN ('ingredient', 'equipment') AND e.merged_into_id IS NULL
     ORDER BY e.id`,
    [LOCALE],
  );
  const aliases = new Map<number, string[]>();
  for (const row of db.all("SELECT entry_id, text_normalized FROM aliases")) {
    const list = aliases.get(Number(row["entry_id"])) ?? [];
    list.push(text(row["text_normalized"]));
    aliases.set(Number(row["entry_id"]), list);
  }
  return rows.map((row) => ({
    id: Number(row["id"]),
    aliases: aliases.get(Number(row["id"])) ?? [],
    kind: row["kind"] as "ingredient" | "equipment",
    nameEn: text(row["name_en"]),
    label: maybeText(row["label"]) ?? text(row["name_en"]),
    parentId: maybeNumber(row["parent_id"]),
    isStaple: Number(row["is_staple"]) === 1,
    isBasic: Number(row["is_basic"]) === 1,
    availability: row["availability"] as Availability,
    owned: Number(row["owned"]) === 1,
  }));
}

const CARD_SQL = `
  SELECT r.id, r.title_original, r.total_min, r.servings, r.course, lt.text AS title,
         COALESCE(s.display_title, s.raw_title) AS source
  FROM recipes AS r
  LEFT JOIN localized_texts AS lt ON lt.owner_kind = 'recipe_title' AND lt.owner_id = r.id AND lt.locale = ?
  LEFT JOIN sources AS s ON s.id = r.source_id`;

function card(row: Row): RecipeCard {
  return {
    id: Number(row["id"]),
    title: maybeText(row["title"]) ?? text(row["title_original"]),
    titleOriginal: text(row["title_original"]),
    totalMin: maybeNumber(row["total_min"]),
    servings: maybeNumber(row["servings"]),
    source: maybeText(row["source"]) ?? "",
    course: maybeText(row["course"]),
  };
}

export function loadRecipeCards(db: Database): Map<RecipeId, RecipeCard> {
  return new Map(db.all(`${CARD_SQL} ORDER BY r.id`, [LOCALE]).map((row) => [Number(row["id"]), card(row)]));
}

export function loadRecipeDetail(db: Database, id: RecipeId): RecipeDetail | null {
  const [row] = db.all(
    `SELECT r.id, r.title_original, r.total_min, r.servings, r.prep_min, r.locator, r.description_original,
            lt.text AS title, ld.text AS description, COALESCE(s.display_title, s.raw_title) AS source
     FROM recipes AS r
     LEFT JOIN localized_texts AS lt ON lt.owner_kind = 'recipe_title' AND lt.owner_id = r.id AND lt.locale = ?
     LEFT JOIN localized_texts AS ld ON ld.owner_kind = 'recipe_description' AND ld.owner_id = r.id AND ld.locale = ?
     LEFT JOIN sources AS s ON s.id = r.source_id
     WHERE r.id = ?`,
    [LOCALE, LOCALE, id],
  );
  if (row === undefined) return null;

  const lines = (section: "ingredients" | "steps"): RecipeLine[] =>
    db
      .all(
        `SELECT l.text_original, t.text AS translated,
                (SELECT COUNT(*) FROM recipe_lines AS rl WHERE rl.raw_line_id = l.id AND rl.ingredient_id IS NULL AND rl.sub_recipe_id IS NULL) AS open,
                (SELECT group_concat(rl.ingredient_id) FROM recipe_lines AS rl WHERE rl.raw_line_id = l.id AND rl.ingredient_id IS NOT NULL) AS ids
         FROM raw_lines AS l
         LEFT JOIN localized_texts AS t ON t.owner_kind = 'raw_line' AND t.owner_id = l.id AND t.locale = ?
         WHERE l.recipe_id = ? AND l.section = ? ORDER BY l.position`,
        [LOCALE, id, section],
      )
      .map((line) => ({
        original: text(line["text_original"]),
        translated: maybeText(line["translated"]),
        ingredientIds: maybeText(line["ids"])?.split(",").map(Number) ?? [],
        unresolved: Number(line["open"]) > 0,
      }));

  const equipment = db
    .all(
      `SELECT COALESCE(l.name, e.name_en) AS label, re.provenance
       FROM recipe_equipment AS re JOIN vocab_entries AS e ON e.id = re.equipment_id
       LEFT JOIN vocab_labels AS l ON l.entry_id = e.id AND l.locale = ?
       WHERE re.recipe_id = ? ORDER BY re.id`,
      [LOCALE, id],
    )
    .map((item) => ({ label: text(item["label"]), listed: item["provenance"] === "listed" }));

  const attributes = db
    .all(
      `SELECT COALESCE(l.name, e.name_en) AS label, a.value, a.unit
       FROM attributes AS a JOIN vocab_entries AS e ON e.id = a.key_id
       LEFT JOIN vocab_labels AS l ON l.entry_id = e.id AND l.locale = ?
       WHERE a.recipe_id = ? ORDER BY e.name_en`,
      [LOCALE, id],
    )
    .map((item) => ({ label: text(item["label"]), value: text(item["value"]), unit: maybeText(item["unit"]) }));

  return {
    ...card(row),
    description: maybeText(row["description"]),
    descriptionOriginal: maybeText(row["description_original"]),
    prepMin: maybeNumber(row["prep_min"]),
    locator: maybeText(row["locator"]),
    ingredients: lines("ingredients"),
    steps: lines("steps"),
    equipment,
    attributes,
  };
}

export function summarize(db: Database): DatabaseSummary {
  const count = (sql: string) => Number(db.all(sql)[0]?.["n"] ?? 0);
  return {
    recipes: count("SELECT COUNT(*) AS n FROM recipes WHERE duplicate_of IS NULL"),
    ingredients: count("SELECT COUNT(*) AS n FROM vocab_entries WHERE kind = 'ingredient' AND merged_into_id IS NULL"),
    equipment: count("SELECT COUNT(*) AS n FROM vocab_entries WHERE kind = 'equipment' AND merged_into_id IS NULL"),
    sources: count("SELECT COUNT(*) AS n FROM sources"),
  };
}

/** Writes to the person's own tables: availability and equipment. Ingest never overwrites these. */
export function setAvailability(db: AppDatabase, entryId: EntryId, availability: Availability): void {
  db.run(
    `INSERT INTO ingredient_info (entry_id, availability, availability_changed_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
     ON CONFLICT (entry_id) DO UPDATE SET availability = excluded.availability, availability_changed_at = excluded.availability_changed_at`,
    [entryId, availability],
  );
}

export function setOwned(db: AppDatabase, entryId: EntryId, owned: boolean): void {
  db.run(
    `INSERT INTO equipment_info (entry_id, owned) VALUES (?, ?) ON CONFLICT (entry_id) DO UPDATE SET owned = excluded.owned`,
    [entryId, owned ? 1 : 0],
  );
}

/** What the person set in the app (what they can buy, what they own), by English name so it can move to a new database file. */
export interface PersonalState {
  availability: [name: string, availability: Availability][];
  owned: string[];
}

export function readPersonalState(db: AppDatabase): PersonalState {
  return {
    availability: db
      .all(
        `SELECT e.name_en, i.availability FROM ingredient_info AS i JOIN vocab_entries AS e ON e.id = i.entry_id
         WHERE e.kind = 'ingredient' AND e.merged_into_id IS NULL AND i.availability <> 'unknown'`,
      )
      .map((row): [string, Availability] => [text(row["name_en"]), row["availability"] as Availability]),
    owned: db
      .all(
        `SELECT e.name_en FROM equipment_info AS q JOIN vocab_entries AS e ON e.id = q.entry_id
         WHERE e.kind = 'equipment' AND e.merged_into_id IS NULL AND q.owned = 1`,
      )
      .map((row) => text(row["name_en"])),
  };
}

/** Put a previous database's answers into a newly imported one; names the new file doesn't have are dropped. */
export function applyPersonalState(db: AppDatabase, state: PersonalState): void {
  const ids = new Map(
    db.all("SELECT id, kind, name_en FROM vocab_entries WHERE merged_into_id IS NULL").map((row) => [`${text(row["kind"])}:${text(row["name_en"])}`, Number(row["id"])]),
  );
  for (const [name, availability] of state.availability) {
    const id = ids.get(`ingredient:${name}`);
    if (id !== undefined) setAvailability(db, id, availability);
  }
  for (const name of state.owned) {
    const id = ids.get(`equipment:${name}`);
    if (id !== undefined) setOwned(db, id, true);
  }
}
