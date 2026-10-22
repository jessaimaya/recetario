import { useSignal } from "@preact/signals";

import { courseLabel } from "../core/courses";
import { cards, maxMissing, results, searchData } from "../store";
import { fold } from "../text";
import { CourseSelect, Empty, Segmented, count, minutes, shortTitle } from "./parts";
import { useRail } from "./rail";

type Sort = "az" | "time";

export function Recipes() {
  useRail("Recetario ▪ Recetas ▪ Todas");
  const query = useSignal("");
  const sort = useSignal<Sort>("az");
  const source = useSignal("");
  const course = useSignal<string | undefined>(undefined);

  const duplicates = new Set(searchData.value?.recipes.filter((recipe) => recipe.duplicateOf !== null).map((recipe) => recipe.id));
  const missing = new Map(results.value.map((result) => [result.recipeId, result.missing.length]));
  const all = [...cards.value.values()].filter((recipe) => !duplicates.has(recipe.id));
  const courseTotals = new Map<string, number>();
  for (const recipe of all) if (recipe.course) courseTotals.set(recipe.course, (courseTotals.get(recipe.course) ?? 0) + 1);
  const sources = [...new Set(all.map((recipe) => shortTitle(recipe.source)).filter((name) => name !== ""))];

  const needle = fold(query.value.trim());
  const list = all
    .filter((recipe) => course.value === undefined || recipe.course === course.value)
    .filter((recipe) => source.value === "" || shortTitle(recipe.source) === source.value)
    .filter((recipe) => needle === "" || fold(recipe.title).includes(needle) || fold(recipe.titleOriginal).includes(needle))
    .sort((a, b) =>
      sort.value === "time"
        ? (a.totalMin ?? Infinity) - (b.totalMin ?? Infinity) || sortKey(a.title).localeCompare(sortKey(b.title), "es")
        : sortKey(a.title).localeCompare(sortKey(b.title), "es"),
    );

  const groups = new Map<string, typeof list>();
  for (const recipe of list) {
    const key = sort.value === "az" ? initial(recipe.title) : "";
    groups.set(key, [...(groups.get(key) ?? []), recipe]);
  }

  return (
    <div class="page">
      <h1>Recetas</h1>
      <p class="lede">Todas las que tengo guardadas: {count(all.length, "receta", "recetas")}.</p>
      <div class="toolbar">
        <input type="search" placeholder="Buscar por nombre…" value={query.value} onInput={(event) => (query.value = event.currentTarget.value)} aria-label="Buscar receta" />
        <Segmented label="Orden" value={sort.value} options={[{ value: "az", text: "A–Z" }, { value: "time", text: "Más rápidas" }]} onChange={(value) => (sort.value = value)} />
        <CourseSelect value={course.value} onChange={(value) => (course.value = value)} counts={courseTotals} />
        {sources.length > 1 && (
          <label class="select">
            <span class="eyebrow">Libro</span>
            <select value={source.value} onChange={(event) => (source.value = event.currentTarget.value)}>
              <option value="">Todos los libros ({sources.length})</option>
              {sources.map((name) => (
                <option key={name} value={name}>
                  {name} · {all.filter((recipe) => shortTitle(recipe.source) === name).length}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {list.length === 0 ? (
        <Empty title="No encontré recetas con ese nombre" />
      ) : (
        [...groups.entries()].map(([letter, recipes]) => (
          <section key={letter || "all"} class="letter-group">
            {letter !== "" && <h2 class="letter" aria-label={`Recetas con ${letter}`}>{letter}</h2>}
            <ul class="recipes">
              {recipes.map((recipe) => {
                const gap = missing.get(recipe.id);
                const status = gap === undefined ? null : gap === 0 ? "Lista para cocinar" : `Te ${gap === 1 ? "falta 1 ingrediente" : `faltan ${gap}`}`;
                return (
                  <li key={recipe.id}>
                    <a href={`#/receta/${recipe.id}`}>
                      <span class="title">{recipe.title}</span>
                      <span class="meta">
                        {[courseLabel(recipe.course), minutes(recipe.totalMin), recipe.servings ? count(recipe.servings, "porción", "porciones") : null].filter(Boolean).join(" · ")}
                      </span>
                      {status && <span class="missing">{status}</span>}
                    </a>
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
      <p class="note">
        {maxMissing.value === 0 ? "Solo marco las que están listas." : `Marco las que te faltan hasta ${count(maxMissing.value, "ingrediente", "ingredientes")}.`} Cámbialo en <a href="#/">Cocinar</a>.
      </p>
    </div>
  );
}

/** A title without the quotes and punctuation it starts with, so '"Arroz" de coliflor' files under A. */
function sortKey(title: string): string {
  return title.replace(/^[^\p{L}\p{N}]+/u, "");
}

/** First letter of a title for the A–Z index, without accents; a title that starts with a digit goes under "#". */
function initial(title: string): string {
  const letter = fold(sortKey(title)).charAt(0).toUpperCase();
  return /[A-Z]/.test(letter) ? letter : "#";
}
