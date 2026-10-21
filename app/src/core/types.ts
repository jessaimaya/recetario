// Shapes shared by the match core, the database loader and (later) the natural-language search tool.
// The core is pure: no I/O, no globals. Everything it needs comes in as arguments.

export type EntryId = number;
export type RecipeId = number;

export type Availability = "obtainable" | "unobtainable" | "unknown";

/** One thing a recipe needs. Staples, basic equipment and optional lines are already left out. */
export type Requirement =
  /** Any of these ingredients will do. `null` is a line not resolved to the vocabulary yet; `label` is its text for display and `search` the text to match words against. */
  | { kind: "ingredient"; anyOf: (EntryId | null)[]; label?: string; search?: string }
  /** Any of these pieces of equipment will do. */
  | { kind: "equipment"; anyOf: EntryId[] }
  /** Another recipe used as an ingredient: have what it produces, or have what it needs. */
  | { kind: "recipe"; recipeId: RecipeId };

export interface RecipeForMatch {
  id: RecipeId;
  /** The ingredient this recipe makes (the pesto recipe makes pesto), if any. */
  produces: EntryId | null;
  requirements: Requirement[];
}

export interface RecipeForSearch extends RecipeForMatch {
  totalMin: number | null;
  /** Average cook-log rating, 1 to 5. */
  rating: number | null;
  duplicateOf: RecipeId | null;
}

/** Personal state: what I can buy and what I own, plus the hierarchy that relates entries. */
export interface Catalog {
  /** child → parent, for ingredients and equipment ("cheddar cheese" → "cheese"). */
  parentOf: ReadonlyMap<EntryId, EntryId>;
  /** Ingredients not in the map are unknown. */
  availability: ReadonlyMap<EntryId, Availability>;
  owned: ReadonlySet<EntryId>;
}

export interface SearchData {
  catalog: Catalog;
  recipes: RecipeForSearch[];
}

/** One requirement I don't have on hand. Buying any of `anyOf` would cover it. */
export interface Need {
  anyOf: EntryId[];
  /** A line the vocabulary doesn't know yet (`anyOf` is empty): its text. Counted as missing unless the person's words cover it. */
  unresolved?: string;
}

export type BlockReason =
  /** No alternative can be bought. */
  | "unobtainable"
  /** No alternative is known to be obtainable, and some are unknown: worth asking about. */
  | "unclassified"
  /** A line isn't resolved to the vocabulary, or a sub-recipe is missing or circular. */
  | "unresolved"
  /** No alternative is owned. */
  | "equipment"
  /** Every alternative is excluded by the search. */
  | "excluded";

/** `ids` are vocabulary entries, except for "unresolved" (a sub-recipe that is missing or circular), where they are the recipes to review. */
export interface Block {
  reason: BlockReason;
  ids: number[];
}

export type MatchResult = { kind: "match"; missing: Need[] } | { kind: "blocked"; blocks: Block[] };

/** Also the input schema of the natural-language search tool (M9), so keep it plain JSON. */
export interface SearchFilters {
  onHand: EntryId[];
  /** What the person has, as lower-case words without accents (names, aliases, things typed that the vocabulary lacks):
   *  lets "kale" cover a recipe line the vocabulary hasn't resolved yet. */
  onHandText?: string[];
  maxMissing: number;
  maxTotalMin?: number;
  excludeIngredients?: EntryId[];
}

export interface RankedRecipe {
  recipeId: RecipeId;
  missing: Need[];
}
