import type {
  Block,
  BlockReason,
  Catalog,
  EntryId,
  MatchResult,
  Need,
  RecipeForMatch,
  RecipeId,
  Requirement,
} from "./types";

export interface MatchInput {
  catalog: Catalog;
  /** Every recipe a sub-recipe line may point at. */
  recipes: ReadonlyMap<RecipeId, RecipeForMatch>;
  onHand: Iterable<EntryId>;
  exclude?: Iterable<EntryId>;
}

const BLOCK_ORDER: BlockReason[] = ["excluded", "unobtainable", "equipment", "unresolved", "unclassified"];

interface Evaluation {
  missing: Need[];
  blocks: Block[];
}

/**
 * Prepares the hierarchy lookups once and returns a function that matches one recipe.
 *
 * Hierarchy: having (or owning, or being able to buy) a child covers a requirement for its parent
 * (cheddar covers "cheese"), never the other way round. Excluding a parent excludes its children.
 */
export function createMatcher(input: MatchInput): (recipe: RecipeForMatch) => MatchResult {
  const { catalog, recipes } = input;
  const ancestorsOrSelf = (id: EntryId): EntryId[] => {
    const chain = [id];
    for (let p = catalog.parentOf.get(id); p !== undefined && !chain.includes(p); p = catalog.parentOf.get(p)) {
      chain.push(p);
    }
    return chain;
  };
  const covering = (ids: Iterable<EntryId>) => new Set([...ids].flatMap(ancestorsOrSelf));

  const onHand = covering(input.onHand);
  const owned = covering(catalog.owned);
  const obtainable = covering(
    [...catalog.availability].filter(([, availability]) => availability === "obtainable").map(([id]) => id),
  );
  const exclude = new Set(input.exclude ?? []);
  const isExcluded = (id: EntryId) => ancestorsOrSelf(id).some((a) => exclude.has(a));
  const availabilityOf = (id: EntryId) =>
    obtainable.has(id) ? "obtainable" : (catalog.availability.get(id) ?? "unknown");

  function ingredient(recipe: RecipeForMatch, anyOf: (EntryId | null)[], out: Evaluation): void {
    const candidates = anyOf.filter((id) => id === null || !isExcluded(id));
    if (candidates.length === 0) {
      out.blocks.push({ reason: "excluded", ids: anyOf.filter((id) => id !== null) });
      return;
    }
    const resolved = candidates.filter((id) => id !== null);
    if (resolved.some((id) => onHand.has(id))) return;

    const buyable = resolved.filter((id) => availabilityOf(id) === "obtainable");
    if (buyable.length > 0) {
      out.missing.push({ anyOf: buyable });
      return;
    }
    const unknown = resolved.filter((id) => availabilityOf(id) === "unknown");
    if (unknown.length > 0) out.blocks.push({ reason: "unclassified", ids: unknown });
    else if (resolved.length < candidates.length) out.blocks.push({ reason: "unresolved", ids: [recipe.id] });
    else out.blocks.push({ reason: "unobtainable", ids: resolved });
  }

  function subRecipe(recipe: RecipeForMatch, recipeId: RecipeId, stack: RecipeId[], out: Evaluation): void {
    const sub = recipes.get(recipeId);
    if (sub === undefined || stack.includes(recipeId)) {
      out.blocks.push({ reason: "unresolved", ids: [recipe.id] });
      return;
    }
    const product = sub.produces !== null && !isExcluded(sub.produces) ? sub.produces : null;
    if (product !== null && onHand.has(product)) return;

    const made = evaluate(sub, [...stack, recipeId]);
    const bought = product !== null && obtainable.has(product) ? [{ anyOf: [product] }] : null;
    if (made.blocks.length > 0) {
      if (bought) out.missing.push(...bought);
      else out.blocks.push(...made.blocks);
    } else if (bought && bought.length < made.missing.length) {
      out.missing.push(...bought);
    } else {
      // On a tie, making it wins: the shopping list is just as long and the ingredients are fresher.
      out.missing.push(...made.missing);
    }
  }

  function requirement(recipe: RecipeForMatch, req: Requirement, stack: RecipeId[], out: Evaluation): void {
    switch (req.kind) {
      case "ingredient":
        return ingredient(recipe, req.anyOf, out);
      case "equipment":
        if (!req.anyOf.some((id) => owned.has(id))) out.blocks.push({ reason: "equipment", ids: req.anyOf });
        return;
      case "recipe":
        return subRecipe(recipe, req.recipeId, stack, out);
    }
  }

  function evaluate(recipe: RecipeForMatch, stack: RecipeId[]): Evaluation {
    const out: Evaluation = { missing: [], blocks: [] };
    for (const req of recipe.requirements) requirement(recipe, req, stack, out);
    return out;
  }

  return (recipe) => {
    const { missing, blocks } = evaluate(recipe, [recipe.id]);
    return blocks.length > 0 ? { kind: "blocked", blocks: mergeBlocks(blocks) } : { kind: "match", missing: uniqueNeeds(missing) };
  };
}

export function match(recipe: RecipeForMatch, input: MatchInput): MatchResult {
  return createMatcher(input)(recipe);
}

function mergeBlocks(blocks: Block[]): Block[] {
  return BLOCK_ORDER.flatMap((reason) => {
    const ids = new Set(blocks.filter((b) => b.reason === reason).flatMap((b) => b.ids));
    return ids.size > 0 ? [{ reason, ids: [...ids].sort((a, b) => a - b) }] : [];
  });
}

function uniqueNeeds(needs: Need[]): Need[] {
  const byKey = new Map<string, Need>();
  for (const need of needs) {
    const anyOf = [...new Set(need.anyOf)].sort((a, b) => a - b);
    byKey.set(anyOf.join(","), { anyOf });
  }
  return [...byKey.values()];
}
