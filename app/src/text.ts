/** Lower case without accents, so "jalapeño" is found by typing "jalapeno". */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}
