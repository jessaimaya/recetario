import { signal } from "@preact/signals";

import { devDatabaseAvailable, errorMessage, importDevDatabase, status } from "../store";
import { Data, ImportBox } from "./Data";
import { Equipment } from "./Equipment";
import { Ingredients } from "./Ingredients";
import { Recipe } from "./Recipe";
import { railText, useRail } from "./rail";
import { Search } from "./Search";

const route = signal(location.hash || "#/");
addEventListener("hashchange", () => {
  route.value = location.hash || "#/";
  scrollTo(0, 0);
});

const TABS = [
  { href: "#/", text: "Cocinar" },
  { href: "#/despensa", text: "Despensa" },
  { href: "#/cocina", text: "Cocina" },
  { href: "#/datos", text: "Datos" },
];

export function App() {
  const current = route.value;
  return (
    <>
      <div class="sheet">
        <aside class="rail" aria-hidden="true"><span>{railText.value}</span></aside>
        <main>{status.value === "ready" ? <Screen path={current} /> : <Welcome />}</main>
      </div>
      {status.value === "ready" && (
        <nav class="tabs" aria-label="Secciones">
          {TABS.map((tab) => (
            <a key={tab.href} href={tab.href} aria-current={isActive(current, tab.href) ? "page" : undefined}>
              {tab.text}
            </a>
          ))}
        </nav>
      )}
    </>
  );
}

function isActive(current: string, href: string): boolean {
  if (href === "#/") return current === "#/" || current.startsWith("#/receta");
  return current === href;
}

function Screen({ path }: { path: string }) {
  const recipe = /^#\/receta\/(\d+)$/.exec(path);
  if (recipe) return <Recipe id={Number(recipe[1])} />;
  if (path === "#/despensa") return <Ingredients />;
  if (path === "#/cocina") return <Equipment />;
  if (path === "#/datos") return <Data />;
  return <Search />;
}

function Welcome() {
  useRail("Recetario ▪ Lo que tienes ▪ Lo que cocinas");
  if (status.value === "loading") {
    return <div class="page center"><p class="lede">Abriendo tus recetas…</p></div>;
  }
  return (
    <div class="page welcome">
      <h1>Recetario</h1>
      <p class="lede">Dime qué tienes en casa y te digo qué puedes cocinar. Funciona sin internet y tus datos se quedan en este dispositivo.</p>
      {status.value === "error" && errorMessage.value && <p class="error" role="alert">{errorMessage.value}</p>}
      <ImportBox />
      {import.meta.env.DEV && devDatabaseAvailable.value && (
        <button type="button" class="primary" onClick={() => void importDevDatabase()}>Cargar la base local (solo desarrollo)</button>
      )}
    </div>
  );
}
