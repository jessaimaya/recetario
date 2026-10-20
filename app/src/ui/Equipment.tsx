import { changeOwned, equipment } from "../store";
import { Empty } from "./parts";

export function Equipment() {
  const list = [...equipment.value].sort((a, b) => a.label.localeCompare(b.label, "es"));
  return (
    <div class="page">
      <h1>Cocina</h1>
      <p class="lede">Marca el equipo que tienes. Una receta que lo necesita y no lo tienes no aparece en los resultados.</p>
      {list.length === 0 ? (
        <Empty title="Sin equipo en el vocabulario" />
      ) : (
        <ul class="rows">
          {list.map((entry) => (
            <li key={entry.id} class="row">
              <div class="row-head">
                <div>
                  <strong>{entry.label}</strong>
                  <small>{entry.isBasic ? "básico, lo doy por hecho" : entry.nameEn}</small>
                </div>
                {entry.isBasic ? (
                  <span class="chip on" aria-label="Equipo básico">Siempre</span>
                ) : (
                  <button type="button" class={`chip ${entry.owned ? "on" : ""}`} aria-pressed={entry.owned} onClick={() => changeOwned(entry.id, !entry.owned)}>
                    {entry.owned ? "Lo tengo" : "No lo tengo"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
