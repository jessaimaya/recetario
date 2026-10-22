/** The kinds of dish a recipe can be, as stored (schema 6) and as shown. */
export const COURSES = [
  { value: "plato_fuerte", label: "Plato fuerte" },
  { value: "guarnicion", label: "Guarnición" },
  { value: "entrada", label: "Entrada" },
  { value: "postre", label: "Postre" },
  { value: "aderezo", label: "Aderezo o salsa" },
  { value: "desayuno", label: "Desayuno" },
  { value: "bebida", label: "Bebida" },
  { value: "snack", label: "Snack" },
] as const;

export function courseLabel(course: string | null | undefined): string | null {
  return COURSES.find((item) => item.value === course)?.label ?? null;
}
