-- Recetario schema v1.
-- Booleans are INTEGER 0/1. Timestamps are ISO-8601 UTC text.
-- The schema version lives in PRAGMA user_version and is set by the migration runner.

-- Sources and pipeline bookkeeping -------------------------------------------

CREATE TABLE sources (
    id            INTEGER PRIMARY KEY,
    display_title TEXT,
    raw_title     TEXT NOT NULL,
    author        TEXT,
    isbn          TEXT,
    asin          TEXT,
    file_hash     TEXT NOT NULL UNIQUE,
    format        TEXT NOT NULL CHECK (format IN ('epub', 'pdf')),
    lang          TEXT,
    status        TEXT NOT NULL DEFAULT 'registered'
                  CHECK (status IN ('registered', 'in_progress', 'done', 'failed')),
    ingested_at   TEXT
) STRICT;

CREATE TABLE stage_runs (
    id             INTEGER PRIMARY KEY,
    source_id      INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    recipe_key     TEXT,
    stage          TEXT NOT NULL,
    input_hash     TEXT NOT NULL,
    prompt_version TEXT,
    model          TEXT,
    tokens_in      INTEGER,
    tokens_out     INTEGER,
    web_searches   INTEGER,
    cost_usd       REAL,
    ms             INTEGER,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'cached')),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE INDEX stage_runs_lookup ON stage_runs (source_id, stage, recipe_key);

-- Vocabulary: ingredients, equipment and attribute keys ----------------------

CREATE TABLE vocab_entries (
    id                INTEGER PRIMARY KEY,
    kind              TEXT NOT NULL CHECK (kind IN ('ingredient', 'equipment', 'attribute_key')),
    name_en           TEXT NOT NULL,
    parent_id         INTEGER REFERENCES vocab_entries (id),
    description       TEXT,
    external_off      TEXT,
    external_wikidata TEXT,
    external_fdc      TEXT,
    merged_into_id    INTEGER REFERENCES vocab_entries (id),
    UNIQUE (kind, name_en)
) STRICT;

CREATE INDEX vocab_entries_parent ON vocab_entries (parent_id);

-- A parent or merge target must be the same kind as the entry.
CREATE TRIGGER vocab_entries_same_kind_insert
BEFORE INSERT ON vocab_entries
WHEN (NEW.parent_id IS NOT NULL
      AND (SELECT kind FROM vocab_entries WHERE id = NEW.parent_id) IS NOT NEW.kind)
  OR (NEW.merged_into_id IS NOT NULL
      AND (SELECT kind FROM vocab_entries WHERE id = NEW.merged_into_id) IS NOT NEW.kind)
BEGIN
    SELECT RAISE(ABORT, 'parent and merge target must be the same kind');
END;

CREATE TRIGGER vocab_entries_same_kind_update
BEFORE UPDATE OF kind, parent_id, merged_into_id ON vocab_entries
WHEN (NEW.parent_id IS NOT NULL
      AND (SELECT kind FROM vocab_entries WHERE id = NEW.parent_id) IS NOT NEW.kind)
  OR (NEW.merged_into_id IS NOT NULL
      AND (SELECT kind FROM vocab_entries WHERE id = NEW.merged_into_id) IS NOT NEW.kind)
BEGIN
    SELECT RAISE(ABORT, 'parent and merge target must be the same kind');
END;

CREATE TABLE vocab_labels (
    entry_id INTEGER NOT NULL REFERENCES vocab_entries (id) ON DELETE CASCADE,
    locale   TEXT NOT NULL,
    name     TEXT NOT NULL,
    PRIMARY KEY (entry_id, locale)
) STRICT;

CREATE TABLE aliases (
    kind            TEXT NOT NULL CHECK (kind IN ('ingredient', 'equipment', 'attribute_key')),
    lang            TEXT NOT NULL,
    text_normalized TEXT NOT NULL,
    entry_id        INTEGER NOT NULL REFERENCES vocab_entries (id) ON DELETE CASCADE,
    confirmed       INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    source          TEXT NOT NULL CHECK (source IN ('seed', 'review', 'auto')),
    PRIMARY KEY (kind, lang, text_normalized)
) STRICT;

CREATE TRIGGER aliases_kind_matches_entry
BEFORE INSERT ON aliases
WHEN (SELECT kind FROM vocab_entries WHERE id = NEW.entry_id) IS NOT NEW.kind
BEGIN
    SELECT RAISE(ABORT, 'alias kind must match its entry kind');
END;

CREATE TABLE proposals (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('ingredient', 'equipment', 'attribute_key')),
    term            TEXT NOT NULL,
    context         TEXT,            -- JSON
    action          TEXT NOT NULL CHECK (action IN ('map_to', 'new_entry', 'merge')),
    target_entry_id INTEGER REFERENCES vocab_entries (id),
    new_entry       TEXT,            -- JSON: { name_en, labels, parent }
    confidence      REAL,
    evidence        TEXT,            -- JSON array of { source, ref, snippet }
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'accepted', 'edited', 'rejected')),
    source_id       INTEGER REFERENCES sources (id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_at      TEXT
) STRICT;

CREATE INDEX proposals_open ON proposals (status, kind);

-- Kind-specific data. My decisions (availability, ownership) live here, and load never writes them.

CREATE TABLE ingredient_info (
    entry_id                INTEGER PRIMARY KEY REFERENCES vocab_entries (id) ON DELETE CASCADE,
    availability            TEXT NOT NULL DEFAULT 'unknown'
                            CHECK (availability IN ('obtainable', 'unobtainable', 'unknown')),
    availability_changed_at TEXT,
    is_staple               INTEGER NOT NULL DEFAULT 0 CHECK (is_staple IN (0, 1))
) STRICT;

CREATE TABLE equipment_info (
    entry_id INTEGER PRIMARY KEY REFERENCES vocab_entries (id) ON DELETE CASCADE,
    owned    INTEGER NOT NULL DEFAULT 0 CHECK (owned IN (0, 1)),
    is_basic INTEGER NOT NULL DEFAULT 0 CHECK (is_basic IN (0, 1))
) STRICT;

CREATE TABLE key_info (
    entry_id    INTEGER PRIMARY KEY REFERENCES vocab_entries (id) ON DELETE CASCADE,
    value_type  TEXT NOT NULL CHECK (value_type IN ('number', 'text', 'bool', 'duration')),
    unit        TEXT,
    column_name TEXT    -- set when the key was promoted to a recipes column
) STRICT;

-- Recipes --------------------------------------------------------------------

CREATE TABLE recipes (
    id                   INTEGER PRIMARY KEY,
    source_id            INTEGER REFERENCES sources (id) ON DELETE CASCADE,  -- NULL: entered by hand
    locator              TEXT,
    title_original       TEXT NOT NULL,
    description_original TEXT,
    servings             REAL,
    prep_min             INTEGER,
    total_min            INTEGER,
    status               TEXT NOT NULL DEFAULT 'needs_review' CHECK (status IN ('needs_review', 'ok')),
    duplicate_of         INTEGER REFERENCES recipes (id) ON DELETE SET NULL,
    UNIQUE (source_id, locator)
) STRICT;

CREATE TABLE raw_lines (
    id            INTEGER PRIMARY KEY,
    recipe_id     INTEGER NOT NULL REFERENCES recipes (id) ON DELETE CASCADE,
    section       TEXT NOT NULL CHECK (section IN ('ingredients', 'steps')),
    position      INTEGER NOT NULL,
    text_original TEXT NOT NULL,
    UNIQUE (recipe_id, section, position)
) STRICT;

-- One raw line becomes N recipe lines ("salt and pepper" = 2).
-- ingredient_id and sub_recipe_id both NULL means not resolved yet (the app treats it as unclassified).
-- alt_group: lines of the same recipe sharing a number are alternatives (OR).
-- A sub-recipe line can't be part of an OR-group: an OR between AND-sets can't be expressed flatly.
CREATE TABLE recipe_lines (
    id                   INTEGER PRIMARY KEY,
    raw_line_id          INTEGER NOT NULL REFERENCES raw_lines (id) ON DELETE CASCADE,
    ingredient_id        INTEGER REFERENCES vocab_entries (id),
    sub_recipe_id        INTEGER REFERENCES recipes (id),
    qty                  REAL,
    unit                 TEXT,
    preparation_original TEXT,
    is_optional          INTEGER NOT NULL DEFAULT 0 CHECK (is_optional IN (0, 1)),
    alt_group            INTEGER,
    CHECK (ingredient_id IS NULL OR sub_recipe_id IS NULL),
    CHECK (sub_recipe_id IS NULL OR alt_group IS NULL)
) STRICT;

CREATE INDEX recipe_lines_raw ON recipe_lines (raw_line_id);

CREATE TRIGGER recipe_lines_ingredient_kind
BEFORE INSERT ON recipe_lines
WHEN NEW.ingredient_id IS NOT NULL
 AND (SELECT kind FROM vocab_entries WHERE id = NEW.ingredient_id) IS NOT 'ingredient'
BEGIN
    SELECT RAISE(ABORT, 'recipe_lines.ingredient_id must reference an ingredient');
END;

CREATE TABLE recipe_equipment (
    id           INTEGER PRIMARY KEY,
    recipe_id    INTEGER NOT NULL REFERENCES recipes (id) ON DELETE CASCADE,
    equipment_id INTEGER NOT NULL REFERENCES vocab_entries (id),
    is_optional  INTEGER NOT NULL DEFAULT 0 CHECK (is_optional IN (0, 1)),
    alt_group    INTEGER,
    provenance   TEXT NOT NULL CHECK (provenance IN ('listed', 'inferred')),
    raw_line_id  INTEGER REFERENCES raw_lines (id) ON DELETE SET NULL
) STRICT;

CREATE INDEX recipe_equipment_recipe ON recipe_equipment (recipe_id);

CREATE TRIGGER recipe_equipment_kind
BEFORE INSERT ON recipe_equipment
WHEN (SELECT kind FROM vocab_entries WHERE id = NEW.equipment_id) IS NOT 'equipment'
BEGIN
    SELECT RAISE(ABORT, 'recipe_equipment.equipment_id must reference equipment');
END;

-- Translations of recipe text, per locale. The original text always stays in its own table.
CREATE TABLE localized_texts (
    owner_kind     TEXT NOT NULL
                   CHECK (owner_kind IN ('recipe_title', 'recipe_description', 'raw_line', 'preparation')),
    owner_id       INTEGER NOT NULL,
    locale         TEXT NOT NULL,
    text           TEXT NOT NULL,
    provenance     TEXT NOT NULL CHECK (provenance IN ('book', 'computed', 'manual')),
    prompt_version TEXT,
    PRIMARY KEY (owner_kind, owner_id, locale)
) STRICT;

-- Sparse properties whose key is an attribute_key entry. Manual values always win on reload.
CREATE TABLE attributes (
    recipe_id  INTEGER NOT NULL REFERENCES recipes (id) ON DELETE CASCADE,
    key_id     INTEGER NOT NULL REFERENCES vocab_entries (id),
    value      TEXT NOT NULL,
    unit       TEXT,
    per        TEXT CHECK (per IN ('serving', 'recipe')),
    provenance TEXT NOT NULL CHECK (provenance IN ('book', 'computed', 'manual')),
    confidence REAL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (recipe_id, key_id)
) STRICT;

CREATE TRIGGER attributes_key_kind
BEFORE INSERT ON attributes
WHEN (SELECT kind FROM vocab_entries WHERE id = NEW.key_id) IS NOT 'attribute_key'
BEGIN
    SELECT RAISE(ABORT, 'attributes.key_id must reference an attribute_key');
END;

CREATE TABLE cook_log (
    id        INTEGER PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipes (id) ON DELETE CASCADE,
    cooked_on TEXT NOT NULL,
    rating    INTEGER CHECK (rating BETWEEN 1 AND 5),
    note      TEXT
) STRICT;

-- What a recipe needs -------------------------------------------------------
-- One row per (recipe, requirement alternative). Rows sharing req_key are one requirement satisfied by
-- any of them (an OR-group). Excluded: optional lines, and any requirement where one alternative is a
-- staple (ingredients) or basic (equipment), since that requirement is always met.
-- Sub-recipes are expanded: their requirements count for the recipe that uses them.
-- entry_id NULL means an unresolved line.
CREATE VIEW recipe_requirements AS
WITH RECURSIVE
expansion (root_id, recipe_id, depth) AS (
    SELECT id, id, 0 FROM recipes
    UNION ALL
    SELECT e.root_id, rl.sub_recipe_id, e.depth + 1
    FROM expansion AS e
    JOIN raw_lines AS r ON r.recipe_id = e.recipe_id
    JOIN recipe_lines AS rl ON rl.raw_line_id = r.id
    WHERE rl.sub_recipe_id IS NOT NULL
      AND rl.is_optional = 0
      AND e.depth < 5
),
ingredient_alternatives AS (
    SELECT e.root_id AS recipe_id,
           e.recipe_id || ':i:' || COALESCE('g' || rl.alt_group, 'l' || rl.id) AS req_key,
           rl.ingredient_id AS entry_id,
           COALESCE(ii.is_staple, 0) AS always_met
    FROM expansion AS e
    JOIN raw_lines AS r ON r.recipe_id = e.recipe_id
    JOIN recipe_lines AS rl ON rl.raw_line_id = r.id
    LEFT JOIN ingredient_info AS ii ON ii.entry_id = rl.ingredient_id
    WHERE rl.sub_recipe_id IS NULL
      AND rl.is_optional = 0
),
equipment_alternatives AS (
    SELECT e.root_id AS recipe_id,
           e.recipe_id || ':e:' || COALESCE('g' || re.alt_group, 'l' || re.id) AS req_key,
           re.equipment_id AS entry_id,
           COALESCE(ei.is_basic, 0) AS always_met
    FROM expansion AS e
    JOIN recipe_equipment AS re ON re.recipe_id = e.recipe_id
    LEFT JOIN equipment_info AS ei ON ei.entry_id = re.equipment_id
    WHERE re.is_optional = 0
),
alternatives AS (
    SELECT recipe_id, 'ingredient' AS kind, req_key, entry_id, always_met FROM ingredient_alternatives
    UNION ALL
    SELECT recipe_id, 'equipment' AS kind, req_key, entry_id, always_met FROM equipment_alternatives
)
SELECT DISTINCT a.recipe_id, a.kind, a.req_key, a.entry_id
FROM alternatives AS a
WHERE NOT EXISTS (
    SELECT 1 FROM alternatives AS b
    WHERE b.recipe_id = a.recipe_id AND b.req_key = a.req_key AND b.always_met = 1
);
