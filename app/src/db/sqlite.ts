import sqlite3InitModule, { type Database as Oo1Database, type Sqlite3Static } from "@sqlite.org/sqlite-wasm";

import type { Database, Row } from "./load";

/** The database in the browser: an in-memory SQLite opened from file bytes, and written back out as bytes. */
export interface AppDatabase extends Database {
  run(sql: string, bind?: unknown[]): void;
  /** The whole database as a file, to save on the device or to download. */
  exportBytes(): Uint8Array;
  close(): void;
}

let sqlite3: Promise<Sqlite3Static> | undefined;

export async function openDatabase(bytes: Uint8Array): Promise<AppDatabase> {
  sqlite3 ??= sqlite3InitModule();
  const api = await sqlite3;
  const db: Oo1Database = new api.oo1.DB();
  const pointer = api.wasm.allocFromTypedArray(bytes);
  const code = api.capi.sqlite3_deserialize(
    db.pointer!,
    "main",
    pointer,
    bytes.length,
    bytes.length,
    api.capi.SQLITE_DESERIALIZE_FREEONCLOSE | api.capi.SQLITE_DESERIALIZE_RESIZEABLE,
  );
  if (code !== 0) {
    db.close();
    throw new Error("this file is not a SQLite database");
  }
  return {
    all(sql, bind) {
      return db.exec({ sql, bind: bind as never, rowMode: "object", returnValue: "resultRows" }) as Row[];
    },
    run(sql, bind) {
      db.exec({ sql, bind: bind as never });
    },
    exportBytes() {
      return api.capi.sqlite3_js_db_export(db.pointer!);
    },
    close() {
      db.close();
    },
  };
}
