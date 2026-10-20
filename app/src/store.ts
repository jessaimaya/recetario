import { batch, computed, effect, signal } from "@preact/signals";

import { blockedCounts, classificationQuestions, questionsToAsk, search } from "./core/search";
import type { Availability, EntryId, RecipeId, SearchData } from "./core/types";
import { loadSearchData } from "./db/load";
import { deleteValue, readValue, writeValue } from "./db/persist";
import { type DatabaseSummary, type Entry, type RecipeCard, loadEntries, loadRecipeCards, setAvailability, setOwned, summarize } from "./db/queries";
import { type AppDatabase, openDatabase } from "./db/sqlite";
import { resolveTerm, splitTerms } from "./ui/find";

export type Status = "loading" | "empty" | "ready" | "error";

export const status = signal<Status>("loading");
export const errorMessage = signal<string | null>(null);
export const entries = signal<Entry[]>([]);
export const cards = signal<Map<RecipeId, RecipeCard>>(new Map());
export const searchData = signal<SearchData | null>(null);
export const summary = signal<DatabaseSummary | null>(null);
export const devDatabaseAvailable = signal(false);

/** What I have at home right now, as entry ids. Kept by English name so it survives replacing the database file. */
export const pantry = signal<ReadonlySet<EntryId>>(new Set());
/** Things typed into the pantry that the vocabulary doesn't have yet; kept, and matched again whenever the database changes. */
export const pantryExtras = signal<readonly string[]>([]);
export const pantryNote = signal<string | null>(null);
let noteParts: string[] = [];
let noteAt = 0;
export const maxMissing = signal(readNumber("maxMissing") ?? 1);
export const maxTotalMin = signal<number | undefined>(readNumber("maxTotalMin"));
/** Questions dismissed for this visit. */
export const dismissed = signal<ReadonlySet<string>>(new Set());

let database: AppDatabase | null = null;

export const entryById = computed(() => new Map(entries.value.map((entry) => [entry.id, entry])));
export const ingredients = computed(() => entries.value.filter((entry) => entry.kind === "ingredient"));
export const equipment = computed(() => entries.value.filter((entry) => entry.kind === "equipment"));

const filters = computed(() => ({ onHand: [...pantry.value], maxMissing: maxMissing.value, maxTotalMin: maxTotalMin.value }));
export const results = computed(() => (searchData.value ? search(filters.value, searchData.value) : []));
export const blocked = computed(() => (searchData.value ? blockedCounts(filters.value, searchData.value) : {}));
export const haveQuestions = computed(() => questionsToAsk(results.value, 8).filter((q) => !dismissed.value.has(`have:${q.entryId}`) && entryById.value.get(q.entryId)?.availability === "obtainable"));
export const classifyQuestions = computed(() =>
  searchData.value ? classificationQuestions(filters.value, searchData.value, 8).filter((q) => !dismissed.value.has(`classify:${q.entryId}`)) : [],
);

function readNumber(key: string): number | undefined {
  try {
    const value = localStorage.getItem(key);
    return value === null || value === "" ? undefined : Number(value);
  } catch {
    return undefined;
  }
}

function remember(key: string, value: number | undefined): void {
  try {
    localStorage.setItem(key, value === undefined ? "" : String(value));
  } catch {
    // a private window can refuse storage; the setting just won't stick
  }
}

export function setMaxMissing(value: number): void {
  maxMissing.value = value;
  remember("maxMissing", value);
}

export function setMaxTotalMin(value: number | undefined): void {
  maxTotalMin.value = value;
  remember("maxTotalMin", value);
}

export async function bootstrap(): Promise<void> {
  try {
    const bytes = await readValue<Uint8Array>("db");
    if (bytes) {
      await attach(bytes);
      return;
    }
    status.value = "empty";
    if (import.meta.env.DEV) devDatabaseAvailable.value = (await fetch("/dev/recetario.db", { method: "HEAD" })).ok;
  } catch (error) {
    fail(error);
  }
}

export async function importBytes(bytes: Uint8Array): Promise<void> {
  try {
    await attach(bytes);
    await writeValue("db", bytes);
  } catch (error) {
    fail(error);
  }
}

export async function importFile(file: File): Promise<void> {
  await importBytes(new Uint8Array(await file.arrayBuffer()));
}

export async function importDevDatabase(): Promise<void> {
  const response = await fetch("/dev/recetario.db");
  await importBytes(new Uint8Array(await response.arrayBuffer()));
}

async function attach(bytes: Uint8Array): Promise<void> {
  const next = await openDatabase(bytes);
  try {
    const data = loadSearchData(next);
    database?.close();
    database = next;
    batch(() => {
      searchData.value = data;
      entries.value = loadEntries(next);
      cards.value = loadRecipeCards(next);
      summary.value = summarize(next);
      status.value = "ready";
      errorMessage.value = null;
    });
  } catch (error) {
    next.close();
    throw error;
  }
  await restorePantry();
}

function fail(error: unknown): void {
  errorMessage.value = error instanceof Error ? error.message : String(error);
  status.value = "error";
}

async function restorePantry(): Promise<void> {
  const names = (await readValue<string[]>("pantry")) ?? [];
  const byName = new Map(ingredients.value.map((entry) => [entry.nameEn, entry.id]));
  const restored = new Set(names.flatMap((name) => byName.get(name) ?? []));
  // Terms the vocabulary couldn't place last time may fit now (a review pass added them).
  const stillUnknown: string[] = [];
  for (const term of (await readValue<string[]>("pantryExtras")) ?? []) {
    const entry = resolveTerm(term, ingredients.value);
    if (entry && !entry.isStaple) restored.add(entry.id);
    else if (!entry) stillUnknown.push(term);
  }
  batch(() => {
    pantry.value = restored;
    pantryExtras.value = stillUnknown;
  });
  savePantry();
}

function savePantry(): void {
  const names = [...pantry.value].flatMap((id) => entryById.value.get(id)?.nameEn ?? []);
  void writeValue("pantry", names);
  void writeValue("pantryExtras", [...pantryExtras.value]);
}

/** Adds everything typed into the pantry field ("brócoli, tortillas y huevo") and says what happened to each term. */
export function addTerms(input: string): void {
  const added: string[] = [];
  const staples: string[] = [];
  const unknown: string[] = [];
  const next = new Set(pantry.value);
  for (const term of splitTerms(input)) {
    const entry = resolveTerm(term, ingredients.value);
    if (entry === null) unknown.push(term);
    else if (entry.isStaple) staples.push(entry.label);
    else {
      next.add(entry.id);
      added.push(entry.label);
    }
  }
  const extras = [...pantryExtras.value];
  for (const term of unknown) if (!extras.some((known) => known.toLowerCase() === term.toLowerCase())) extras.push(term);
  const parts = [
    added.length > 0 ? `Agregué: ${[...new Set(added)].join(", ")}.` : null,
    staples.length > 0 ? `Ya doy por hechos: ${[...new Set(staples)].join(", ")}.` : null,
    unknown.length > 0
      ? `Sin reconocer: ${unknown.join(", ")}. Aún no ${unknown.length === 1 ? "está en el vocabulario; lo guardo y se unirá solo" : "están en el vocabulario; los guardo y se unirán solos"} cuando exista${unknown.length === 1 ? "" : "n"}.`
      : null,
  ].filter((part) => part !== null);
  // Typing "brócoli, tortillas" commits twice (at the comma, then at Enter); what happened to each belongs in one message.
  noteParts = Date.now() - noteAt < 8000 ? [...noteParts, ...parts].slice(-4) : parts;
  noteAt = Date.now();
  batch(() => {
    pantry.value = next;
    pantryExtras.value = extras;
    pantryNote.value = noteParts.length > 0 ? noteParts.join(" ") : null;
  });
  savePantry();
}

export function removeExtra(term: string): void {
  pantryExtras.value = pantryExtras.value.filter((known) => known !== term);
  savePantry();
}

export function togglePantry(id: EntryId, on?: boolean): void {
  const next = new Set(pantry.value);
  const wanted = on ?? !next.has(id);
  if (wanted) next.add(id);
  else next.delete(id);
  pantry.value = next;
  savePantry();
}

export function dismiss(key: string): void {
  dismissed.value = new Set(dismissed.value).add(key);
}

let saveTimer: ReturnType<typeof setTimeout> | undefined;
/** The database lives in memory, so after an edit the file is written back to the device a moment later. */
function saveSoon(): void {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    if (database) void writeValue("db", database.exportBytes());
  }, 400);
}

function refreshEntries(): void {
  if (!database) return;
  batch(() => {
    entries.value = loadEntries(database!);
    searchData.value = loadSearchData(database!);
  });
}

export function changeAvailability(id: EntryId, availability: Availability): void {
  if (!database) return;
  setAvailability(database, id, availability);
  refreshEntries();
  saveSoon();
}

export function changeOwned(id: EntryId, owned: boolean): void {
  if (!database) return;
  setOwned(database, id, owned);
  refreshEntries();
  saveSoon();
}

export function readDatabase(): AppDatabase | null {
  return database;
}

export function downloadDatabase(): void {
  if (!database) return;
  const blob = new Blob([database.exportBytes() as BlobPart], { type: "application/vnd.sqlite3" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "recetario.db";
  link.click();
  URL.revokeObjectURL(link.href);
}

export async function resetData(): Promise<void> {
  database?.close();
  database = null;
  await Promise.all([deleteValue("db"), deleteValue("pantry"), deleteValue("pantryExtras")]);
  batch(() => {
    entries.value = [];
    cards.value = new Map();
    searchData.value = null;
    summary.value = null;
    pantry.value = new Set();
    pantryExtras.value = [];
    status.value = "empty";
  });
}


/** Questions on screen, kept in place: answering one shows a confirmation there instead of reshuffling the others. */
const SLOTS = 2;
const CONFIRMATION_MS = 3000;

export interface AnsweredQuestion {
  text: string;
  undo: () => void;
}

export const answered = signal<ReadonlyMap<string, AnsweredQuestion>>(new Map());
export const slots = signal<string[]>([]);

export const openQuestions = computed(() => [
  ...haveQuestions.value.map((question) => ({ key: `have:${question.entryId}`, kind: "have" as const, entryId: question.entryId, count: question.completes })),
  ...classifyQuestions.value.map((question) => ({ key: `classify:${question.entryId}`, kind: "classify" as const, entryId: question.entryId, count: question.appearsIn })),
]);

effect(() => {
  const open = new Map(openQuestions.value.map((question) => [question.key, question]));
  const previous = slots.peek();
  // A slot keeps its question while it is still open (or just answered); a freed slot takes the next question, so nothing else moves.
  const kept = Array.from({ length: SLOTS }, (_, index) => {
    const key = previous[index];
    return key !== undefined && (open.has(key) || answered.value.has(key)) ? key : null;
  });
  const pool = [...open.keys()].filter((key) => !kept.includes(key));
  const next = kept.map((key) => key ?? pool.shift() ?? null).filter((key): key is string => key !== null);
  if (next.join() !== previous.join()) slots.value = next;
});

function confirm(key: string, text: string, undo: () => void): void {
  const timer = setTimeout(() => clearAnswer(key), CONFIRMATION_MS);
  answered.value = new Map(answered.value).set(key, {
    text,
    undo: () => {
      clearTimeout(timer);
      undo();
      clearAnswer(key);
    },
  });
}

function clearAnswer(key: string): void {
  const next = new Map(answered.value);
  next.delete(key);
  answered.value = next;
}

export function answerAvailability(entryId: EntryId, availability: "obtainable" | "unobtainable"): void {
  const entry = entryById.value.get(entryId);
  if (!entry) return;
  // one batch: the slot must see the confirmation and the change together, or it would drop the answered question
  batch(() => {
    changeAvailability(entryId, availability);
    confirm(`classify:${entryId}`, `${entry.label}: ${availability === "obtainable" ? "sí se consigue" : "no se consigue"}`, () => changeAvailability(entryId, "unknown"));
  });
}

export function answerHave(entryId: EntryId): void {
  const entry = entryById.value.get(entryId);
  if (!entry) return;
  batch(() => {
    togglePantry(entryId, true);
    confirm(`have:${entryId}`, `Anotado: tienes ${entry.label}`, () => togglePantry(entryId, false));
  });
}
