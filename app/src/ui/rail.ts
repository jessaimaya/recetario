import { signal } from "@preact/signals";
import { useEffect } from "preact/hooks";

/** The vertical tagline at the left edge; each screen says what it is. */
export const railText = signal("Recetario");

export function useRail(text: string): void {
  useEffect(() => {
    railText.value = text;
  }, [text]);
}
