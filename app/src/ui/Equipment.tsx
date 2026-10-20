import { blocked, changeOwned, equipment } from "../store";
import { DoneBar, Empty, Switch, count } from "./parts";
import { useRail } from "./rail";

export function Equipment() {
  useRail("Recetario ▪ Cocina ▪ Tu equipo");
  const list = [...equipment.value].sort((a, b) => a.label.localeCompare(b.label, "es"));
  const have = list.filter((entry) => entry.isBasic || entry.owned).length;
  const stuck = blocked.value.equipment ?? 0;
  return (
    <div class="page">
      <h1>Cocina</h1>
      <p class="lede">
        Activa el equipo que tienes. Una receta que lo necesita y no lo tienes no aparece en los resultados.
        Los cambios se guardan solos.
      </p>
      <p class="tally" aria-live="polite">
        Tienes {have} de {list.length}.{" "}
        {stuck > 0 ? `${count(stuck, "receta sigue", "recetas siguen")} bloqueada${stuck === 1 ? "" : "s"} por equipo.` : "Ninguna receta está bloqueada por equipo."}
      </p>
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
                  <span class="always">Siempre</span>
                ) : (
                  <Switch checked={entry.owned} label="Lo tengo" onChange={(checked) => changeOwned(entry.id, checked)} />
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      <DoneBar />
    </div>
  );
}
