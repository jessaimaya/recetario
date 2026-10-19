// Builds contract.db with the Python ingest CLI (schema + vocabulary + fixture recipes), so the TS tests
// read exactly what Python writes. Needs uv on PATH.
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import type { TestProject } from "vitest/node";

declare module "vitest" {
  export interface ProvidedContext {
    contractDb: string;
  }
}

export default function setup(project: TestProject) {
  const dir = mkdtempSync(join(tmpdir(), "recetario-contract-"));
  const path = join(dir, "contract.db");
  const ingest = resolve(import.meta.dirname, "../../ingest");
  try {
    execFileSync(
      "uv",
      ["run", "--project", ingest, "recetario-ingest", "db", "init", "--fresh", "--fixtures", "--db", path],
      { stdio: "pipe" },
    );
  } catch (error) {
    const detail = error instanceof Error && "stderr" in error ? String(error.stderr) : String(error);
    throw new Error(`could not build contract.db with the Python ingest (is uv installed?)\n${detail}`);
  }
  project.provide("contractDb", path);
  return () => rmSync(dir, { recursive: true, force: true });
}
