import { useSignal } from "@preact/signals";

import type { RankedRecipe } from "../core/types";
import {
  addTerms, answerAvailability, answerHave, answered, blocked, cards, dismiss, entryById, ingredients, maxMissing,
  maxTotalMin, openQuestions, pantry, pantryExtras, pantryNote, removeExtra, results, setMaxMissing, setMaxTotalMin, slots, togglePantry,
} from "../store";
import { findEntries } from "./find";
import { Card, Empty, Segmented, count, minutes, needText } from "./parts";
import { useRail } from "./rail";

const GROUP_LIMIT = 30;

export function Search() {
  useRail("Recetario ▪ ¿Qué cocino hoy? ▪ Con lo que tienes");
  const found = results.value;
  const none = found.length === 0;
  return (
    <div class="page">
      <h1>¿Qué cocino hoy?</h1>
      <Pantry />
      <div class="filters">
        <label>
          <span>Te puede faltar</span>
          <Segmented
            label="Ingredientes que faltan"
            value={maxMissing.value}
            options={[0, 1, 2, 3].map((n) => ({ value: n, text: n === 0 ? "nada" : String(n) }))}
            onChange={setMaxMissing}
          />
        </label>
        <label>
          <span>Tiempo total</span>
          <Segmented
            label="Tiempo total"
            value={maxTotalMin.value}
            options={[{ value: undefined, text: "cualquiera" }, { value: 30, text: "≤ 30 min" }, { value: 60, text: "≤ 1 h" }]}
            onChange={setMaxTotalMin}
          />
        </label>
      </div>
      <Questions />
      {none ? <NoResults /> : <Groups found={found} />}
      <HiddenNote />
      <p class="note"><a href="#/recetas">Ver todas las recetas →</a></p>
    </div>
  );
}

function Pantry() {
  const query = useSignal("");
  const chosen = [...pantry.value].map((id) => entryById.value.get(id)).filter((entry) => entry !== undefined);
  const typed = query.value.trim();
  const suggestions = typed === "" ? [] : findEntries(typed, ingredients.value.filter((entry) => !entry.isStaple && !pantry.value.has(entry.id)));

  const commit = (text: string) => {
    if (text.trim() === "") return;
    addTerms(text);
    query.value = "";
  };
  const onInput = (value: string) => {
    // a comma ends a term: take what is complete, keep what is still being typed
    const cut = Math.max(value.lastIndexOf(","), value.lastIndexOf(";"));
    if (cut >= 0) {
      addTerms(value.slice(0, cut));
      query.value = value.slice(cut + 1).trimStart();
    } else {
      query.value = value;
    }
  };

  return (
    <section aria-labelledby="pantry-title" class="pantry">
      <h2 id="pantry-title">En casa</h2>
      <ul class="chips">
        {chosen.map((entry) => (
          <li key={entry.id}>
            <button type="button" class="chip on" onClick={() => togglePantry(entry.id, false)} aria-label={`Quitar ${entry.label}`} title="Toca para quitar">
              {entry.label}
            </button>
          </li>
        ))}
        {pantryExtras.value.map((term) => (
          <li key={`extra:${term}`}>
            <button type="button" class="chip pending" onClick={() => removeExtra(term)} aria-label={`Quitar ${term}, sin reconocer`} title="Aún no está en el vocabulario. Toca para quitar">
              {term}
            </button>
          </li>
        ))}
      </ul>
      <form
        class="combo"
        onSubmit={(event) => {
          event.preventDefault();
          commit(query.value);
        }}
      >
        <div class="combo-row">
          <input
            type="search"
            placeholder="Escribe lo que tienes…"
            value={query.value}
            onInput={(event) => onInput(event.currentTarget.value)}
            aria-label="Agregar ingredientes"
            enterkeyhint="done"
            autocomplete="off"
          />
          <button type="submit" class="primary add" disabled={typed === ""}>Agregar</button>
        </div>
        {suggestions.length > 0 && (
          <ul class="suggestions">
            {suggestions.map((entry) => (
              <li key={entry.id}>
                <button type="button" onClick={() => commit(entry.label)}>
                  {entry.label}
                  <small>{entry.nameEn}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </form>
      {pantryNote.value && <p class="note" aria-live="polite">{pantryNote.value}</p>}
    </section>
  );
}

function Questions() {
  const open = new Map(openQuestions.value.map((question) => [question.key, question]));
  if (slots.value.length === 0) return null;
  return (
    <div class="questions">
      {slots.value.map((key) => {
        const done = answered.value.get(key);
        if (done) {
          return (
            <Card key={key} tone="ask">
              <p aria-live="polite">
                <strong>✓ {done.text}</strong>
              </p>
              <div class="actions">
                <button type="button" class="quiet" onClick={done.undo}>Deshacer</button>
              </div>
            </Card>
          );
        }
        const question = open.get(key);
        const entry = question && entryById.value.get(question.entryId);
        if (!question || !entry) return null;
        return question.kind === "have" ? (
          <Card key={key} tone="ask">
            <p>
              <strong>¿Tienes {entry.label}?</strong>
              <small> Con eso completas {count(question.count, "receta", "recetas")}.</small>
            </p>
            <div class="actions">
              <button type="button" class="primary" onClick={() => answerHave(entry.id)}>Sí, tengo</button>
              <button type="button" onClick={() => dismiss(key)}>No</button>
            </div>
          </Card>
        ) : (
          <Card key={key} tone="ask">
            <p>
              <strong>¿{entry.label} se consigue por aquí?</strong>
              <small> Está en {count(question.count, "receta", "recetas")} que hoy no puedo evaluar.</small>
            </p>
            <div class="actions">
              <button type="button" class="primary" onClick={() => answerAvailability(entry.id, "obtainable")}>Sí se consigue</button>
              <button type="button" onClick={() => answerAvailability(entry.id, "unobtainable")}>No se consigue</button>
              <button type="button" class="quiet" onClick={() => dismiss(key)}>Luego</button>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function Groups({ found }: { found: RankedRecipe[] }) {
  const groups = new Map<number, RankedRecipe[]>();
  for (const result of found) groups.set(result.missing.length, [...(groups.get(result.missing.length) ?? []), result]);
  return (
    <>
      {[...groups.entries()].map(([missing, list]) => (
        <Group key={missing} missing={missing} list={list} />
      ))}
    </>
  );
}

function Group({ missing, list }: { missing: number; list: RankedRecipe[] }) {
  const all = useSignal(false);
  const shown = all.value ? list : list.slice(0, GROUP_LIMIT);
  const title = missing === 0 ? "Listas para cocinar" : missing === 1 ? "Te falta 1 ingrediente" : `Te faltan ${missing} ingredientes`;
  return (
    <section class="group">
      <h2>
        {title} <span class="count">{list.length}</span>
      </h2>
      <ul class="recipes">
        {shown.map((result) => {
          const recipe = cards.value.get(result.recipeId);
          if (!recipe) return null;
          return (
            <li key={result.recipeId}>
              <a href={`#/receta/${recipe.id}`}>
                <span class="title">{recipe.title}</span>
                <span class="meta">
                  {[minutes(recipe.totalMin), recipe.servings ? count(recipe.servings, "porción", "porciones") : null].filter(Boolean).join(" · ")}
                </span>
                {result.missing.length > 0 && <span class="missing">Falta: {result.missing.map(needText).join(", ")}</span>}
              </a>
            </li>
          );
        })}
      </ul>
      {list.length > shown.length && (
        <button type="button" class="more" onClick={() => (all.value = true)}>Ver las {list.length}</button>
      )}
    </section>
  );
}

function NoResults() {
  if (pantry.value.size === 0) {
    return (
      <Empty title="Empieza con lo que tienes">
        <p>Agrega arriba los ingredientes que hay en tu casa y te muestro lo que puedes cocinar.</p>
      </Empty>
    );
  }
  return (
    <Empty title="Nada por ahora">
      <p>Prueba con que te falten más ingredientes, o responde las preguntas de arriba para que pueda evaluar más recetas.</p>
    </Empty>
  );
}

function HiddenNote() {
  const counts = blocked.value;
  const parts = [
    counts.unclassified ? `${counts.unclassified} esperan que me digas si sus ingredientes se consiguen por aquí` : null,
    counts.unresolved ? `${counts.unresolved} tienen ingredientes que el vocabulario aún no reconoce (falta revisarlos al importar)` : null,
    counts.equipment ? `${counts.equipment} necesitan equipo que no tienes` : null,
    counts.unobtainable ? `${counts.unobtainable} llevan algo que no se consigue` : null,
  ].filter((part) => part !== null);
  if (parts.length === 0) return null;
  return (
    <Card tone="note">
      <h2>Recetas que no se muestran</h2>
      <ul>
        {parts.map((part) => (
          <li key={part}>{part}</li>
        ))}
      </ul>
      <p>
        <a href="#/despensa">Despensa</a> · <a href="#/cocina">Cocina</a>
      </p>
    </Card>
  );
}
