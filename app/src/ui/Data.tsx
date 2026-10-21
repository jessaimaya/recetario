import { useSignal } from "@preact/signals";

import { devDatabaseAvailable, downloadDatabase, errorMessage, importDevDatabase, importFile, resetData, resetDismissed, status, summary } from "../store";
import { Card } from "./parts";
import { useRail } from "./rail";

export function Data() {
  useRail("Recetario ▪ Datos ▪ En este dispositivo");
  const confirming = useSignal(false);
  const info = summary.value;
  return (
    <div class="page">
      <h1>Datos</h1>
      <p class="lede">
        Tu base de datos vive solo en este dispositivo. Nada se sube a ningún servidor.
      </p>
      {info && (
        <Card>
          <dl class="attributes">
            <div><dt>Recetas</dt><dd>{info.recipes}</dd></div>
            <div><dt>Ingredientes</dt><dd>{info.ingredients}</dd></div>
            <div><dt>Equipo</dt><dd>{info.equipment}</dd></div>
            <div><dt>Libros</dt><dd>{info.sources}</dd></div>
          </dl>
        </Card>
      )}
      <ImportBox />
      {status.value === "ready" && (
        <section>
          <h2>Guardar una copia</h2>
          <p class="lede">Lo que marcas aquí (qué se consigue, tu equipo) se guarda en el archivo. Descárgalo para llevarlo de vuelta al ingest.</p>
          <button type="button" onClick={downloadDatabase}>Descargar recetario.db</button>
        </section>
      )}
      {status.value === "ready" && (
        <section>
          <h2>Preguntas descartadas</h2>
          <p class="lede">Lo que contestas con «Luego» o «No» no se vuelve a preguntar. Aquí puedes hacer que se pregunte otra vez.</p>
          <button type="button" onClick={resetDismissed}>Volver a preguntar todo</button>
        </section>
      )}
      {status.value === "ready" && (
        <section>
          <h2>Borrar de este dispositivo</h2>
          {confirming.value ? (
            <div class="actions">
              <button type="button" class="danger" onClick={() => { confirming.value = false; void resetData(); }}>Sí, borrar todo</button>
              <button type="button" onClick={() => (confirming.value = false)}>Cancelar</button>
            </div>
          ) : (
            <button type="button" onClick={() => (confirming.value = true)}>Borrar la base de datos</button>
          )}
        </section>
      )}
      {errorMessage.value && <p class="error" role="alert">{errorMessage.value}</p>}
      {import.meta.env.DEV && devDatabaseAvailable.value && status.value !== "ready" && (
        <button type="button" onClick={() => void importDevDatabase()}>Cargar la base local (solo desarrollo)</button>
      )}
    </div>
  );
}

export function ImportBox() {
  const over = useSignal(false);
  const pick = (file: File | undefined) => {
    if (file) void importFile(file);
  };
  return (
    <section>
      <h2>Importar una base de datos</h2>
      <label
        class={`drop ${over.value ? "over" : ""}`}
        onDragOver={(event) => { event.preventDefault(); over.value = true; }}
        onDragLeave={() => (over.value = false)}
        onDrop={(event) => { event.preventDefault(); over.value = false; pick(event.dataTransfer?.files[0]); }}
      >
        <strong>Elige tu archivo recetario.db</strong>
        <span>o suéltalo aquí. Lo genera el comando <code>recetario-ingest</code>.</span>
        <input type="file" accept=".db,.sqlite,.sqlite3,application/vnd.sqlite3,application/octet-stream" onChange={(event) => pick(event.currentTarget.files?.[0])} />
      </label>
    </section>
  );
}
