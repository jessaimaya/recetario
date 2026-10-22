import { fold } from "../text";
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
  Substitution,
} from "./types";

export interface MatchInput {
  catalog: Catalog;
  /** Every recipe a sub-recipe line may point at. */
  recipes: ReadonlyMap<RecipeId, RecipeForMatch>;
  onHand: Iterable<EntryId>;
  /** Words for what is on hand, folded (see `fold`); used only for lines the vocabulary hasn't resolved. */
  onHandText?: Iterable<string>;
  exclude?: Iterable<EntryId>;
  /** Let members of an ingredient's substitute group stand in for it. Default true. */
  substitutes?: boolean;
}

const BLOCK_ORDER: BlockReason[] = ["excluded", "unobtainable", "equipment", "unresolved", "unclassified"];

interface Evaluation {
  missing: Need[];
  blocks: Block[];
  substitutions: Substitution[];
}

/**
 * Prepares the hierarchy lookups once and returns a function that matches one recipe.
 *
 * Hierarchy: having (or owning, or being able to buy) a child covers a requirement for its parent
 * (cheddar covers "cheese"), never the other way round. Excluding a parent excludes its children.
 *
 * Substitutes: entries in the same substitute group stand in for each other ("canola oil" for "coconut oil"), on hand
 * or to buy, unless `substitutes` is false. A substitute on hand is reported in the result.
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
  const onHandWords = [...(input.onHandText ?? [])].filter((word) => word !== "");
  /** An unresolved line is covered when one of the person's words appears in it as whole words ("kale" in "2 cups kale"). */
  const coveredByWords = (label: string): boolean => {
    const padded = ` ${fold(label).replace(/[^a-z0-9ñ]+/g, " ").trim()} `;
    return onHandWords.some((word) => padded.includes(` ${word} `) || padded.includes(` ${word}s `) || padded.includes(` ${word}es `));
  };
  const exclude = new Set(input.exclude ?? []);
  const isExcluded = (id: EntryId) => ancestorsOrSelf(id).some((a) => exclude.has(a));

  const groupMembers = new Map<string, EntryId[]>();
  if (input.substitutes !== false) {
    for (const [id, group] of catalog.substituteGroupOf ?? []) groupMembers.set(group, [...(groupMembers.get(group) ?? []), id]);
  }
  /** The other members of the ingredient's substitute group that the search allows. */
  const substitutesOf = (id: EntryId): EntryId[] => {
    const group = catalog.substituteGroupOf?.get(id);
    return group === undefined ? [] : (groupMembers.get(group) ?? []).filter((other) => other !== id && !isExcluded(other));
  };
  const availabilityOf = (id: EntryId) =>
    obtainable.has(id) ? "obtainable" : (catalog.availability.get(id) ?? "unknown");

  function ingredient(anyOf: (EntryId | null)[], label: string | undefined, search: string | undefined, out: Evaluation): void {
    const candidates = anyOf.filter((id) => id === null || !isExcluded(id));
    if (candidates.length === 0) {
      out.blocks.push({ reason: "excluded", ids: anyOf.filter((id) => id !== null) });
      return;
    }
    const resolved = candidates.filter((id) => id !== null);
    if (resolved.some((id) => onHand.has(id))) return;
    for (const wanted of resolved) {
      const using = substitutesOf(wanted).find((other) => onHand.has(other));
      if (using !== undefined) {
        out.substitutions.push({ wanted, using });
        return;
      }
    }
    const hasUnresolved = candidates.length > resolved.length;
    if (hasUnresolved && label !== undefined && coveredByWords(search ?? label)) return;

    // What could be bought to cover it: the ingredient itself first, then its substitutes.
    const pool = [...new Set([...resolved, ...resolved.flatMap(substitutesOf)])];
    const buyable = pool.filter((id) => availabilityOf(id) === "obtainable");
    if (buyable.length > 0) {
      out.missing.push({ anyOf: buyable });
      return;
    }
    // Ask about the ingredient itself first; its substitutes only once it is settled, so one question doesn't become five.
    const unknown = resolved.filter((id) => availabilityOf(id) === "unknown");
    const unknownSubstitutes = pool.filter((id) => availabilityOf(id) === "unknown");
    if (unknown.length > 0) out.blocks.push({ reason: "unclassified", ids: unknown });
    else if (unknownSubstitutes.length > 0) out.blocks.push({ reason: "unclassified", ids: unknownSubstitutes });
    else if (hasUnresolved) out.missing.push({ anyOf: [], unresolved: label ?? "" });
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
      out.substitutions.push(...made.substitutions);
    }
  }

  function requirement(recipe: RecipeForMatch, req: Requirement, stack: RecipeId[], out: Evaluation): void {
    switch (req.kind) {
      case "ingredient":
        return ingredient(req.anyOf, req.label, req.search, out);
      case "equipment":
        if (!req.anyOf.some((id) => owned.has(id))) out.blocks.push({ reason: "equipment", ids: req.anyOf });
        return;
      case "recipe":
        return subRecipe(recipe, req.recipeId, stack, out);
    }
  }

  function evaluate(recipe: RecipeForMatch, stack: RecipeId[]): Evaluation {
    const out: Evaluation = { missing: [], blocks: [], substitutions: [] };
    for (const req of recipe.requirements) requirement(recipe, req, stack, out);
    return out;
  }

  return (recipe) => {
    const { missing, blocks, substitutions } = evaluate(recipe, [recipe.id]);
    if (blocks.length > 0) return { kind: "blocked", blocks: mergeBlocks(blocks) };
    return { kind: "match", missing: uniqueNeeds(missing), ...(substitutions.length > 0 && { substitutions }) };
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
    if (need.unresolved !== undefined) byKey.set(`u:${need.unresolved}`, { anyOf, unresolved: need.unresolved });
    else byKey.set(anyOf.join(","), { anyOf });
  }
  return [...byKey.values()];
}
