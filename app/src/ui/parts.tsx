import type { ComponentChildren } from "preact";

import type { Need } from "../core/types";
import { entryById, results } from "../store";

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

export function needText(need: Need): string {
  return need.anyOf.map((id) => entryById.value.get(id)?.label ?? `#${id}`).join(" o ");
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
