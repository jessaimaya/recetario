import type { ComponentChildren } from "preact";

import type { Need } from "../core/types";
import { entryById } from "../store";

/** Lower case without accents, so "jalapeño" is found by typing "jalapeno". */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

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
  return title.split(/[:[]/)[0]?.trim() || title;
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
