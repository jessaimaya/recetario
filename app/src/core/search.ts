import { createMatcher } from "./match";
import type { EntryId, RankedRecipe, RecipeForSearch, SearchData, SearchFilters } from "./types";

/**
 * Recipes I can make with what's on hand, missing at most `maxMissing` requirements.
 * Blocked recipes and duplicates are left out. Ranked by fewest missing, then quickest, then best rated.
 */
export function search(filters: SearchFilters, data: SearchData): RankedRecipe[] {
  const byId = new Map(data.recipes.map((recipe) => [recipe.id, recipe]));
  const matcher = createMatcher({
    catalog: data.catalog,
    recipes: byId,
    onHand: filters.onHand,
    exclude: filters.excludeIngredients,
  });

  const found: { recipe: RecipeForSearch; ranked: RankedRecipe }[] = [];
  for (const recipe of data.recipes) {
    if (recipe.duplicateOf !== null) continue;
    if (filters.maxTotalMin !== undefined && (recipe.totalMin === null || recipe.totalMin > filters.maxTotalMin)) {
      continue;
    }
    const result = matcher(recipe);
    if (result.kind === "blocked" || result.missing.length > filters.maxMissing) continue;
    found.push({ recipe, ranked: { recipeId: recipe.id, missing: result.missing } });
  }

  found.sort(
    (a, b) =>
      a.ranked.missing.length - b.ranked.missing.length ||
      nullsLast(a.recipe.totalMin, b.recipe.totalMin) ||
      nullsLast(b.recipe.rating, a.recipe.rating) ||
      a.recipe.id - b.recipe.id,
  );
  return found.map(({ ranked }) => ranked);
}

export interface Question {
  entryId: EntryId;
  /** Recipes that would become a full match if I have this. */
  completes: number;
  /** Recipes in the results that are missing it at all. */
  missingIn: number;
}

/**
 * "Do you have pesto?" Ingredients worth confirming before shopping: the ones that would turn the most
 * near misses into full matches. An OR-requirement counts for each of its alternatives.
 */
export function questionsToAsk(results: RankedRecipe[], limit = 5): Question[] {
  const questions = new Map<EntryId, Question>();
  for (const { missing } of results) {
    const ids = new Set(missing.flatMap((need) => need.anyOf));
    for (const entryId of ids) {
      const question = questions.get(entryId) ?? { entryId, completes: 0, missingIn: 0 };
      question.missingIn += 1;
      if (missing.length === 1) question.completes += 1;
      questions.set(entryId, question);
    }
  }
  return [...questions.values()]
    .sort((a, b) => b.completes - a.completes || b.missingIn - a.missingIn || a.entryId - b.entryId)
    .slice(0, limit);
}

function nullsLast(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return a - b;
}
