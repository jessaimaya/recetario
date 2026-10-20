// The app's side of the natural-language search: one POST to the proxy. Everything else (matching names to the
// vocabulary, searching) runs on the device. Without VITE_ASK_URL at build time the feature is simply absent.

import { cleanQuestion, parseAsk, type Ask } from "./spec";

export const ASK_URL: string | null = import.meta.env.VITE_ASK_URL || null;

export type AskOutcome = { ok: true; ask: Ask } | { ok: false; message: string };

const MESSAGES: Record<string, string> = {
  off: "La búsqueda con preguntas está apagada por ahora.",
  rate: "Ya hiciste varias preguntas en esta hora. Prueba más tarde o usa los filtros de abajo.",
  "daily-cap": "Se acabó el cupo de preguntas de hoy. Los filtros de abajo siguen funcionando.",
  unclear: "No entendí la pregunta. Prueba nombrando ingredientes, como «tengo pollo y arroz».",
  question: "La pregunta está vacía o es demasiado larga.",
};

export async function askModel(question: string, url: string | null = ASK_URL, send: typeof fetch = fetch): Promise<AskOutcome> {
  const clean = cleanQuestion(question);
  if (clean === null) return { ok: false, message: MESSAGES.question! };
  if (url === null) return { ok: false, message: MESSAGES.off! };
  let response: Response;
  try {
    response = await send(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question: clean }) });
  } catch {
    return { ok: false, message: "Sin conexión: las preguntas necesitan internet. Los filtros de abajo funcionan sin ella." };
  }
  const body = (await response.json().catch(() => null)) as { ask?: unknown; error?: string } | null;
  if (response.ok) {
    const ask = parseAsk(body?.ask);
    if (ask !== null) return { ok: true, ask };
    return { ok: false, message: MESSAGES.unclear! };
  }
  return { ok: false, message: MESSAGES[body?.error ?? ""] ?? "No pude responder ahora. Prueba otra vez en un momento." };
}
