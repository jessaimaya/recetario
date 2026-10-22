import { batch, computed, effect, signal } from "@preact/signals";

import { blockedCounts, classificationQuestions, questionsToAsk, search } from "./core/search";
import type { Availability, EntryId, RecipeId, SearchData } from "./core/types";
import { loadSearchData } from "./db/load";
import { deleteValue, readValue, writeValue } from "./db/persist";
import { type DatabaseSummary, type Entry, type RecipeCard, applyPersonalState, loadEntries, loadRecipeCards, readPersonalState, setAvailability, setOwned, summarize } from "./db/queries";
import { type AppDatabase, openDatabase } from "./db/sqlite";
import { askModel } from "./nl/client";
import { fold } from "./text";
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
/** Let an ingredient in the same substitute group stand in for the one a recipe names (olive oil for coconut oil). */
/** Only recipes of this course on the search screen; undefined shows every kind of dish. */
export const courseFilter = signal<string | undefined>(readText("course"));
export const allowSubstitutes = signal((readNumber("allowSubstitutes") ?? 1) === 1);
export const maxTotalMin = signal<number | undefined>(readNumber("maxTotalMin"));
/** Questions dismissed for this visit. */
export const dismissed = signal<ReadonlySet<string>>(new Set());

let database: AppDatabase | null = null;

export const entryById = computed(() => new Map(entries.value.map((entry) => [entry.id, entry])));
export const ingredients = computed(() => entries.value.filter((entry) => entry.kind === "ingredient"));
export const equipment = computed(() => entries.value.filter((entry) => entry.kind === "equipment"));

/** A question answered by the model, shown instead of the pantry and the filters until it is cleared. Lives for this visit only. */
export interface Asked {
  question: string;
  have: Entry[];
  exclude: Entry[];
  /** Names the vocabulary has no entry for (or only a staple), so they could not count. */
  unknown: string[];
  maxMissing: number;
  maxTotalMin?: number;
  ignored: string[];
}
export const asked = signal<Asked | null>(null);
export const askStatus = signal<{ busy: boolean; message: string | null }>({ busy: false, message: null });

/** What is on hand in words (names, aliases, and typed terms the vocabulary lacks), so it can cover lines still unresolved. */
function wordsFor(ids: Iterable<EntryId>, extras: readonly string[]): string[] {
  const words = new Set(extras.map(fold));
  for (const id of ids) {
    const entry = entryById.value.get(id);
    if (entry) for (const name of [entry.label, entry.nameEn, ...entry.aliases]) words.add(fold(name));
  }
  return [...words];
}

const filters = computed(() => {
  const question = asked.value;
  if (question === null) {
    return { onHand: [...pantry.value], onHandText: wordsFor(pantry.value, pantryExtras.value), maxMissing: maxMissing.value, maxTotalMin: maxTotalMin.value, allowSubstitutes: allowSubstitutes.value, course: courseFilter.value };
  }
  return {
    onHand: question.have.map((entry) => entry.id),
    onHandText: wordsFor(question.have.map((entry) => entry.id), question.unknown),
    excludeIngredients: question.exclude.map((entry) => entry.id),
    maxMissing: question.maxMissing,
    maxTotalMin: question.maxTotalMin,
    allowSubstitutes: allowSubstitutes.value,
  };
});
/** How many recipes match right now in each course, ignoring the course filter, so the dropdown can say where results are. */
export const courseCounts = computed(() => {
  const counts = new Map<string, number>();
  if (!searchData.value) return counts;
  const byId = new Map(searchData.value.recipes.map((recipe) => [recipe.id, recipe.course ?? ""]));
  for (const result of search({ ...filters.value, course: undefined }, searchData.value)) {
    const course = byId.get(result.recipeId);
    if (course) counts.set(course, (counts.get(course) ?? 0) + 1);
  }
  return counts;
});
export const results = computed(() => (searchData.value ? search(filters.value, searchData.value) : []));
export const blocked = computed(() => (searchData.value ? blockedCounts(filters.value, searchData.value) : {}));
export const haveQuestions = computed(() => questionsToAsk(results.value, 8).filter((q) => !dismissed.value.has(`have:${q.entryId}`) && entryById.value.get(q.entryId)?.availability === "obtainable"));
export const classifyQuestions = computed(() =>
  searchData.value ? classificationQuestions(filters.value, searchData.value, 8).filter((q) => !dismissed.value.has(`classify:${q.entryId}`)) : [],
);

function readText(key: string): string | undefined {
  try {
    return localStorage.getItem(key) || undefined;
  } catch {
    return undefined;
  }
}

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

export function setCourseFilter(value: string | undefined): void {
  courseFilter.value = value;
  try {
    localStorage.setItem("course", value ?? "");
  } catch {
    // a private window can refuse storage; the setting just won't stick
  }
}

export function setAllowSubstitutes(value: boolean): void {
  allowSubstitutes.value = value;
  remember("allowSubstitutes", value ? 1 : 0);
}

export function setMaxTotalMin(value: number | undefined): void {
  maxTotalMin.value = value;
  remember("maxTotalMin", value);
}

/** Ask the model for filters, then match the names it gives against the vocabulary here, on the device. */
export async function askQuestion(question: string): Promise<void> {
  askStatus.value = { busy: true, message: null };
  const outcome = await askModel(question);
  if (!outcome.ok) {
    askStatus.value = { busy: false, message: outcome.message };
    return;
  }
  const unknown: string[] = [];
  const match = (terms: string[], keepStaples: boolean): Entry[] => {
    const found = new Map<EntryId, Entry>();
    for (const term of terms) {
      const entry = resolveTerm(term, ingredients.value);
      if (entry === null || (entry.isStaple && !keepStaples)) {
        if (entry === null) unknown.push(term);
      } else found.set(entry.id, entry);
    }
    return [...found.values()];
  };
  const { ask } = outcome;
  batch(() => {
    asked.value = {
      question,
      have: match(ask.have, false),
      exclude: match(ask.exclude, true),
      unknown,
      maxMissing: ask.maxMissing,
      maxTotalMin: ask.maxTotalMin,
      ignored: ask.ignored,
    };
    askStatus.value = { busy: false, message: null };
  });
}

export function clearAsked(): void {
  batch(() => {
    asked.value = null;
    askStatus.value = { busy: false, message: null };
  });
}

/** Answers read from a stored database this app could no longer open (an older schema), kept for the next import. */
let stalePersonalState: ReturnType<typeof readPersonalState> | null = null;

async function checkDevDatabase(): Promise<void> {
  if (!import.meta.env.DEV) return;
  try {
    devDatabaseAvailable.value = (await fetch("/dev/recetario.db", { method: "HEAD" })).ok;
  } catch {
    devDatabaseAvailable.value = false; // no dev server behind the page: nothing to offer
  }
}

export async function bootstrap(): Promise<void> {
  try {
    const bytes = await readValue<Uint8Array>("db");
    if (bytes) {
      try {
        await attach(bytes);
        await checkDevDatabase();
      } catch (error) {
        await keepAnswers(bytes);
        fail(error);
        await checkDevDatabase();
      }
      return;
    }
    status.value = "empty";
    await checkDevDatabase();
  } catch (error) {
    fail(error);
  }
}

async function keepAnswers(bytes: Uint8Array): Promise<void> {
  try {
    const old = await openDatabase(bytes);
    try {
      stalePersonalState = readPersonalState(old);
    } finally {
      old.close();
    }
  } catch {
    stalePersonalState = null; // not a readable database at all: nothing to carry
  }
}

export async function importBytes(bytes: Uint8Array): Promise<void> {
  try {
    // A new file from ingest knows nothing of what I answered in the app (what I can buy, what I own): bring it along.
    const carried = database ? readPersonalState(database) : stalePersonalState;
    await attach(bytes, carried);
    stalePersonalState = null;
    // what is stored is the new file with the carried answers in it, so a reload right after importing keeps them
    await writeValue("db", database ? database.exportBytes() : bytes);
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

async function attach(bytes: Uint8Array, carried: ReturnType<typeof readPersonalState> | null = null): Promise<void> {
  const next = await openDatabase(bytes);
  try {
    if (carried) applyPersonalState(next, carried);
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
    restoreDismissed();
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
  saveDismissed();
}

function undismiss(key: string): void {
  const next = new Set(dismissed.value);
  next.delete(key);
  dismissed.value = next;
  saveDismissed();
}

/** "Luego" and "No" are remembered between visits, by English name so they survive a new database file. */
function saveDismissed(): void {
  const names = [...dismissed.value].flatMap((key) => {
    const [kind, id] = key.split(":");
    const name = entryById.value.get(Number(id))?.nameEn;
    return name === undefined ? [] : [`${kind}:${name}`];
  });
  try {
    localStorage.setItem("dismissed", JSON.stringify(names));
  } catch {
    // a private window can refuse storage; they will be asked again next time
  }
}

function restoreDismissed(): void {
  let names: string[] = [];
  try {
    names = JSON.parse(localStorage.getItem("dismissed") ?? "[]") as string[];
  } catch {
    return;
  }
  const byName = new Map(ingredients.value.map((entry) => [entry.nameEn, entry.id]));
  dismissed.value = new Set(
    names.flatMap((name) => {
      const [kind, ...rest] = name.split(":");
      const id = byName.get(rest.join(":"));
      return id === undefined ? [] : [`${kind}:${id}`];
    }),
  );
}

/** Ask again the questions that were put off or answered "no". */
export function resetDismissed(): void {
  dismissed.value = new Set();
  saveDismissed();
}

let saveTimer: ReturnType<typeof setTimeout> | undefined;
/** The database lives in memory, so after an edit the file is written back to the device a moment later. */
let savePending = false;

function saveSoon(): void {
  clearTimeout(saveTimer);
  savePending = true;
  saveTimer = setTimeout(saveNow, 400);
}

/** Write the database now. Closing or reloading within the 400 ms wait of `saveSoon` would otherwise lose the last answers. */
function saveNow(): void {
  clearTimeout(saveTimer);
  if (!savePending || !database) return;
  savePending = false;
  void writeValue("db", database.exportBytes());
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveNow();
  });
  addEventListener("pagehide", saveNow);
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
  // "Se consigue" here means "I can buy it, I don't have it now": that settles "¿Tienes X?" too, unless it was dismissed already
  const settlesHave = availability === "obtainable" && !dismissed.value.has(`have:${entryId}`);
  // one batch: the slot must see the confirmation and the change together, or it would drop the answered question
  batch(() => {
    changeAvailability(entryId, availability);
    if (settlesHave) dismiss(`have:${entryId}`);
    confirm(`classify:${entryId}`, `${entry.label}: ${availability === "obtainable" ? "sí se consigue" : "no se consigue"}`, () => {
      changeAvailability(entryId, "unknown");
      if (settlesHave) undismiss(`have:${entryId}`);
    });
  });
}

/** Having it means it can be had: one answer settles both questions, and undoing it takes both back. */
export function answerHaveAndObtainable(entryId: EntryId): void {
  const entry = entryById.value.get(entryId);
  if (!entry) return;
  batch(() => {
    changeAvailability(entryId, "obtainable");
    togglePantry(entryId, true);
    confirm(`classify:${entryId}`, `Anotado: tienes ${entry.label} (y se consigue)`, () => {
      togglePantry(entryId, false);
      changeAvailability(entryId, "unknown");
    });
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
