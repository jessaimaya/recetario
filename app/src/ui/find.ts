import type { Entry } from "../db/queries";
import { fold } from "../text";

/** What a person typed into the pantry field, as separate terms: "brócoli, tortillas y huevo" → three. */
export function splitTerms(input: string): string[] {
  return input
    .split(/[,;\n]|\s+y\s+/i)
    .map((term) => term.trim())
    .filter((term) => term !== "");
}

function variants(term: string): string[] {
  const folded = fold(term).trim();
  const out = new Set([folded]);
  if (folded.length > 3 && folded.endsWith("es")) out.add(folded.slice(0, -2));
  if (folded.length > 3 && folded.endsWith("s")) out.add(folded.slice(0, -1));
  return [...out];
}

function score(entry: Entry, queries: string[]): number {
  const keys = [entry.label, entry.nameEn, ...entry.aliases].map(fold);
  let best = 0;
  for (const query of queries) {
    const words = query.split(/\s+/);
    for (const key of keys) {
      if (key === query) best = Math.max(best, 100);
      else if (key.startsWith(query)) best = Math.max(best, 70);
      else if (words.length > 1 && words.every((word) => key.split(/\s+/).some((part) => part.startsWith(word)))) best = Math.max(best, 55);
      else if (key.includes(query)) best = Math.max(best, 40);
    }
  }
  return best;
}

/** Entries that fit what was typed, best first. Plurals and aliases count ("tortillas" finds "tortilla de maíz"). */
export function findEntries(term: string, entries: readonly Entry[], limit = 6): Entry[] {
  const queries = variants(term);
  if (queries[0] === undefined || queries[0] === "") return [];
  return entries
    .map((entry) => ({ entry, score: score(entry, queries) }))
    .filter((match) => match.score >= 40)
    .sort((a, b) => b.score - a.score || a.entry.label.length - b.entry.label.length || a.entry.label.localeCompare(b.entry.label, "es"))
    .slice(0, limit)
    .map((match) => match.entry);
}

/** The one entry a committed term means, or null when nothing fits well enough. */
export function resolveTerm(term: string, entries: readonly Entry[]): Entry | null {
  return findEntries(term, entries, 1)[0] ?? null;
}
