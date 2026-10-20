import { useSignal } from "@preact/signals";

import { loadRecipeDetail } from "../db/queries";
import { readDatabase } from "../store";
import { Empty, Segmented, count, minutes, shortTitle } from "./parts";

export function Recipe({ id }: { id: number }) {
  const original = useSignal(false);
  const db = readDatabase();
  const recipe = db ? loadRecipeDetail(db, id) : null;
  if (!recipe) {
    return (
      <div class="page">
        <a class="back" href="#/">← Volver</a>
        <Empty title="No encontré esa receta" />
      </div>
    );
  }
  const spanish = !original.value;
  const pick = (line: { original: string; translated: string | null }) => (spanish ? line.translated ?? line.original : line.original);
  const hasTranslation = recipe.title !== recipe.titleOriginal || recipe.steps.some((step) => step.translated);
  const unresolved = recipe.ingredients.filter((line) => line.unresolved).length;

  return (
    <article class="page recipe">
      <a class="back" href="#/">← Volver</a>
      <h1>{spanish ? recipe.title : recipe.titleOriginal}</h1>
      {spanish && recipe.title !== recipe.titleOriginal && <p class="original-title">{recipe.titleOriginal}</p>}
      <p class="meta-line">
        {[
          minutes(recipe.totalMin) && `Total ${minutes(recipe.totalMin)}`,
          minutes(recipe.prepMin) && `Preparación ${minutes(recipe.prepMin)}`,
          recipe.servings ? count(recipe.servings, "porción", "porciones") : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
      {hasTranslation && (
        <Segmented label="Idioma del texto" value={original.value ? "original" : "es"} options={[{ value: "es", text: "Español" }, { value: "original", text: "Original" }]} onChange={(value) => (original.value = value === "original")} />
      )}
      {(spanish ? recipe.description : recipe.descriptionOriginal) && <p class="description">{spanish ? recipe.description : recipe.descriptionOriginal}</p>}

      <h2>Ingredientes</h2>
      <ul class="ingredients">
        {recipe.ingredients.map((line, index) => (
          <li key={index} class={line.unresolved ? "unresolved" : ""}>
            {pick(line)}
            {line.unresolved && <small title="Este ingrediente aún no está en el vocabulario"> · sin reconocer</small>}
          </li>
        ))}
      </ul>
      {unresolved > 0 && <p class="note">{unresolved} {unresolved === 1 ? "ingrediente" : "ingredientes"} sin reconocer: esta receta no sale en las búsquedas hasta revisarlos al importar.</p>}

      {recipe.equipment.length > 0 && (
        <>
          <h2>Equipo</h2>
          <ul class="chips">
            {recipe.equipment.map((item) => (
              <li key={item.label} class="chip">{item.label}</li>
            ))}
          </ul>
        </>
      )}

      <h2>Preparación</h2>
      <ol class="steps">
        {recipe.steps.map((step, index) => (
          <li key={index}>{pick(step)}</li>
        ))}
      </ol>

      {recipe.attributes.length > 0 && (
        <>
          <h2>Información nutrimental</h2>
          <dl class="attributes">
            {recipe.attributes.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>{Number(item.value)}{item.unit ? ` ${item.unit}` : ""}</dd>
              </div>
            ))}
          </dl>
          <p class="note">Por porción, según el libro.</p>
        </>
      )}
      <p class="source">{shortTitle(recipe.source)}</p>
    </article>
  );
}
