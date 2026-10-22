import { splitLine } from "./lines";
import type { ComponentChildren } from "preact";

import { COURSES } from "../core/courses";
import type { Need, Substitution } from "../core/types";
import type { Entry } from "../db/queries";
import { entryById, ingredients, results } from "../store";

export { fold } from "../text";

export function minutes(total: number | null): string | null {
  if (total === null) return null;
  const hours = Math.floor(total / 60);
  const rest = Math.round(total % 60);
  if (hours === 0) return `${rest} min`;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** "1 receta", "2 recetas". */
export function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "Hello! 285 5-Ingredient Side Dish Recipes: Best 5-Ingredient … [Book 1]" → "Hello! 285 5-Ingredient Side Dish Recipes". */
export function shortTitle(title: string): string {
  const short = title.split(/[:[]/)[0]?.trim().replace(/^hello!\s*/i, "");
  return short || title;
}

/** "2 large plantains, peeled and sliced (about 1 lb)" → "large plantains"; "1/3 de taza de trocitos de tocino*" → "trocitos de tocino". */
export function unresolvedName(line: string): string {
  const name = splitLine(line).name.replace(/\([^)]*\)?/g, "").split(/[,;]| - /)[0]?.replace(/[*†‡]/g, "").trim().replace(/^(?:de|del|of)\s+(?:la\s+|el\s+)?/i, "").trim();
  return name || line;
}

export function needText(need: Need): string {
  if (need.unresolved !== undefined) return `${unresolvedName(need.unresolved)} (sin reconocer)`;
  return need.anyOf.map((id) => entryById.value.get(id)?.label ?? `#${id}`).join(" o ");
}

/** The entry's label, with its English name added when another ingredient reads the same ("col rizada (kale)"). */
export function distinctLabel(entry: Entry): string {
  const shared = ingredients.value.some((other) => other.id !== entry.id && other.label === entry.label);
  return shared ? `${entry.label} (${entry.nameEn})` : entry.label;
}

/** A dropdown of the kinds of dish; an empty value means all of them. */
export function CourseSelect(props: { value: string | undefined; onChange: (value: string | undefined) => void; counts?: ReadonlyMap<string, number> }) {
  return (
    <label class="select">
      <span class="eyebrow">Tipo de platillo</span>
      <select value={props.value ?? ""} onChange={(event) => props.onChange(event.currentTarget.value || undefined)}>
        <option value="">Todos</option>
        {COURSES.map((course) => (
          <option key={course.value} value={course.value}>
            {course.label}{props.counts ? ` · ${props.counts.get(course.value) ?? 0}` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

/** "aceite de oliva en lugar de aceite de coco, leche de soya en lugar de leche" */
export function swapText(swaps: Substitution[]): string {
  const label = (id: number) => entryById.value.get(id)?.label ?? `#${id}`;
  return swaps.map((swap) => `${label(swap.using)} en lugar de ${label(swap.wanted)}`).join(", ");
}

export function Segmented<T extends string | number | undefined>(props: {
  label: string;
  value: T;
  options: { value: T; text: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div class="segmented" role="group" aria-label={props.label}>
      {props.options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={option.value === props.value}
          onClick={() => props.onChange(option.value)}
        >
          {option.text}
        </button>
      ))}
    </div>
  );
}

export function Card(props: { children: ComponentChildren; tone?: "ask" | "note" }) {
  return <section class={`card ${props.tone ?? ""}`}>{props.children}</section>;
}

export function Empty(props: { title: string; children?: ComponentChildren }) {
  return (
    <div class="empty">
      <h2>{props.title}</h2>
      {props.children}
    </div>
  );
}

/** An on/off control that shows its state: a native checkbox drawn as a switch, with a fixed label. */
export function Switch(props: { checked: boolean; label: string; onChange: (checked: boolean) => void }) {
  return (
    <label class="switch">
      <input type="checkbox" role="switch" checked={props.checked} onChange={(event) => props.onChange(event.currentTarget.checked)} />
      <span class="track" aria-hidden="true"><span class="thumb" /></span>
      <span class="switch-label">{props.label}</span>
    </label>
  );
}

/** Sticks above the tab bar on the setup screens: changes save by themselves, this is the way forward. */
export function DoneBar() {
  const ready = results.value.filter((result) => result.missing.length === 0).length;
  const near = results.value.length - ready;
  return (
    <div class="donebar">
      <a class="button primary" href="#/">
        Ver recetas
        <small>{ready === 0 ? (near === 0 ? "" : `${count(near, "casi lista", "casi listas")}`) : count(ready, "lista para cocinar", "listas para cocinar")}</small>
      </a>
    </div>
  );
}
