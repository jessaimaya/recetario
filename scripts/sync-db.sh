#!/usr/bin/env bash
# Bring the local database up to date after code or review changes: back it up, apply schema migrations,
# reload the vocabulary seed, rewrite every finished book's recipes from the saved artifacts (no model calls),
# then print where things stand. Safe to re-run.
#
# Usage: scripts/sync-db.sh [--accept CONFIDENCE] [--config path/to/recetario.toml]
#   --accept 0.8  first accept every open vocabulary proposal at or above that confidence (review --accept-above)
set -euo pipefail

cd "$(dirname "$0")/../ingest"
config_args=()
accept=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config_args=(--config "$2"); shift 2 ;;
    --accept) accept="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
cli=(uv run --quiet recetario-ingest)

db_path=$(uv run --quiet python -c '
import sys
from pathlib import Path
from recetario_ingest import config
arg = sys.argv[1:]
print(config.load_config(Path(arg[1]) if arg[:1] == ["--config"] else None).db_path)' "${config_args[@]}")

if [[ -f "$db_path" ]]; then
  backup="$db_path.bak-$(date +%Y%m%dT%H%M%S)"
  cp -p "$db_path" "$backup"
  echo "backup: $backup"
fi

"${cli[@]}" db init --db "$db_path"
if [[ -n "$accept" ]]; then
  "${cli[@]}" review --accept-above "$accept" --apply "${config_args[@]}"
fi
"${cli[@]}" load "${config_args[@]}"

uv run --quiet python - "$db_path" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
one = lambda sql: conn.execute(sql).fetchone()[0]
print()
print(f"recipes:               {one('SELECT COUNT(*) FROM recipes')}")
print(f"ingredient lines:      {one('SELECT COUNT(*) FROM recipe_lines')} "
      f"({one('SELECT COUNT(*) FROM recipe_lines WHERE ingredient_id IS NULL AND sub_recipe_id IS NULL')} unresolved)")
print(f"lines with a variant:  {one('SELECT COUNT(*) FROM recipe_lines WHERE variant IS NOT NULL')}")
open_by_kind = conn.execute("SELECT kind, COUNT(*) FROM proposals WHERE status = 'open' GROUP BY kind").fetchall()
print("open proposals:        " + (", ".join(f"{count} {kind}" for kind, count in open_by_kind) or "none"))
PY
echo
echo "next: in the app, Datos → Actualizar desde la base local. More books: recetario-ingest next, then this script again (--accept 0.8 to take the confident proposals)"
