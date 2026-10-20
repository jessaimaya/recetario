import { useSignal } from "@preact/signals";

import type { Availability } from "../core/types";
import type { Entry } from "../db/queries";
import { changeAvailability, ingredients, pantry, togglePantry } from "../store";
import { DoneBar, Empty, Segmented, Switch, fold } from "./parts";
import { useRail } from "./rail";

type Filter = "all" | "home" | "unknown";

const AVAILABILITY: { value: Availability; text: string }[] = [
  { value: "obtainable", text: "Se consigue" },
  { value: "unobtainable", text: "No se consigue" },
  { value: "unknown", text: "No sé" },
];

export function Ingredients() {
  useRail("Recetario ▪ Despensa ▪ Lo que hay en casa");
  const query = useSignal("");
  const filter = useSignal<Filter>("all");
  const needle = fold(query.value.trim());
  const all = ingredients.value;
  const list = all
    .filter((entry) => !entry.isStaple)
    .filter((entry) => (filter.value === "home" ? pantry.value.has(entry.id) : filter.value === "unknown" ? entry.availability === "unknown" : true))
    .filter((entry) => needle === "" || fold(entry.label).includes(needle) || fold(entry.nameEn).includes(needle))
    .sort((a, b) => a.label.localeCompare(b.label, "es"));
  const staples = all.filter((entry) => entry.isStaple);

  return (
    <div class="page">
      <h1>Despensa</h1>
      <p class="lede">Marca lo que tienes en casa y si cada ingrediente se consigue donde vives. Así sé qué recetas te convienen. Los cambios se guardan solos.</p>
      <div class="toolbar">
        <input type="search" placeholder="Buscar ingrediente" value={query.value} onInput={(event) => (query.value = event.currentTarget.value)} aria-label="Buscar ingrediente" />
        <Segmented
          label="Filtro"
          value={filter.value}
          options={[{ value: "all", text: "Todos" }, { value: "home", text: "En casa" }, { value: "unknown", text: "Sin clasificar" }]}
          onChange={(value) => (filter.value = value)}
        />
      </div>
      {list.length === 0 ? (
        <Empty title="Sin resultados" />
      ) : (
        <ul class="rows">
          {list.map((entry) => (
            <Row key={entry.id} entry={entry} />
          ))}
        </ul>
      )}
      {staples.length > 0 && (
        <p class="lede">
          Básicos que doy por hechos: {staples.map((entry) => entry.label).join(", ")}.
        </p>
      )}
      <DoneBar />
    </div>
  );
}

function Row({ entry }: { entry: Entry }) {
  const home = pantry.value.has(entry.id);
  return (
    <li class="row">
      <div class="row-head">
        <div>
          <strong>{entry.label}</strong>
          <small>{entry.nameEn}</small>
        </div>
        <Switch checked={home} label="En casa" onChange={(checked) => togglePantry(entry.id, checked)} />
      </div>
      <Segmented label={`${entry.label}: disponibilidad`} value={entry.availability} options={AVAILABILITY} onChange={(value) => changeAvailability(entry.id, value)} />
    </li>
  );
}
