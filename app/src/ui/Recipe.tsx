import { useSignal } from "@preact/signals";

import { type RecipeLine, loadRecipeDetail } from "../db/queries";
import { entryById, pantry, readDatabase } from "../store";
import { splitLine } from "./lines";
import { Empty, Segmented, count, minutes, shortTitle } from "./parts";
import { useRail } from "./rail";

/** Back to wherever the person came from (the results or the full list); the results page if the recipe was opened directly. */
function goBack(event: MouseEvent): void {
  if (history.length > 1) {
    event.preventDefault();
    history.back();
  }
}

export function Recipe({ id }: { id: number }) {
  const original = useSignal(false);
  const db = readDatabase();
  const recipe = db ? loadRecipeDetail(db, id) : null;
  const tags = recipe
    ? [shortTitle(recipe.source), minutes(recipe.totalMin), recipe.servings ? count(recipe.servings, "porción", "porciones") : null].filter(Boolean).join(" ▪ ")
    : "Recetario";
  useRail(tags);

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
  const have = (line: RecipeLine) =>
    line.ingredientIds.some((entryId) => pantry.value.has(entryId) || entryById.value.get(entryId)?.isStaple === true);
  const haveCount = recipe.ingredients.filter(have).length;

  return (
    <article class="page recipe">
      <a class="back" href="#/" onClick={goBack}>← Volver</a>
      <p class="eyebrow">
        {[minutes(recipe.totalMin) && `Total ${minutes(recipe.totalMin)}`, minutes(recipe.prepMin) && `Preparación ${minutes(recipe.prepMin)}`]
          .filter(Boolean)
          .join(" ▪ ")}
      </p>
      <h1>{spanish ? recipe.title : recipe.titleOriginal}</h1>
      {spanish && recipe.title !== recipe.titleOriginal && <p class="original-title">{recipe.titleOriginal}</p>}
      {(spanish ? recipe.description : recipe.descriptionOriginal) && (
        <p class="description">{spanish ? recipe.description : recipe.descriptionOriginal}</p>
      )}
      {hasTranslation && (
        <div class="lang">
          <Segmented
            label="Idioma del texto"
            value={original.value ? "original" : "es"}
            options={[{ value: "es", text: "Español" }, { value: "original", text: "Original" }]}
            onChange={(value) => (original.value = value === "original")}
          />
        </div>
      )}

      <hr class="rule" />
      <h2>Ingredientes</h2>
      {haveCount > 0 && (
        <p class="legend">
          <span class="mark">Rodeado</span> lo tienes en casa ({haveCount} de {recipe.ingredients.length})
        </p>
      )}
      <ul class="ingredients">
        {recipe.ingredients.map((line, index) => {
          const parts = splitLine(pick(line));
          const name = have(line) ? <span class={`mark ${parts.name.length > 16 ? "long" : ""}`}>{parts.name}</span> : parts.name;
          return (
            <li key={index} class={`ing ${line.unresolved ? "unresolved" : ""} ${parts.qty === "" ? "noqty" : parts.unit === "" ? "nounit" : ""}`}>
              {parts.qty !== "" && <span class="qty">{parts.qty}</span>}
              {parts.unit !== "" && <span class="unit">{parts.unit}</span>}
              <span class="name" title={line.unresolved ? "Este ingrediente aún no está en el vocabulario" : undefined}>{name}</span>
            </li>
          );
        })}
      </ul>
      {unresolved > 0 && (
        <p class="note">
          Los marcados con · aún no están en el vocabulario ({unresolved}); en la búsqueda cuentan como algo que te falta, salvo que ya lo tengas escrito en tu lista.
        </p>
      )}

      <hr class="rule" />
      <h2>Preparación</h2>
      <ol class="steps">
        {recipe.steps.map((step, index) => (
          <li key={index}>
            <span>{pick(step)}</span>
          </li>
        ))}
      </ol>

      {recipe.equipment.length > 0 && (
        <>
          <hr class="rule" />
          <h2>Equipo</h2>
          <p class="equipment">{recipe.equipment.map((item) => item.label).join(" ▪ ")}</p>
        </>
      )}

      {recipe.attributes.length > 0 && (
        <>
          <hr class="rule" />
          <h2>Por porción</h2>
          <dl class="attributes">
            {recipe.attributes.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>
                  {Number(item.value)}
                  {item.unit ? ` ${item.unit}` : ""}
                </dd>
              </div>
            ))}
          </dl>
        </>
      )}
      <p class="source">{shortTitle(recipe.source)}</p>
    </article>
  );
}
