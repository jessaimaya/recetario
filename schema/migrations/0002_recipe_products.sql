-- A recipe can produce an ingredient (the pesto recipe produces "pesto"), so a recipe that uses it as a
-- sub-recipe is satisfied either by having that ingredient or by everything the sub-recipe needs.
-- That is an OR between AND-sets, which a flat view can't express, so sub-recipes are no longer expanded
-- here: the view returns one 'recipe' row per sub-recipe line and the app core does the expansion.

ALTER TABLE recipes ADD COLUMN produces_id INTEGER REFERENCES vocab_entries (id);

CREATE TRIGGER recipes_produces_kind_insert
BEFORE INSERT ON recipes
WHEN NEW.produces_id IS NOT NULL
 AND (SELECT kind FROM vocab_entries WHERE id = NEW.produces_id) IS NOT 'ingredient'
BEGIN
    SELECT RAISE(ABORT, 'recipes.produces_id must reference an ingredient');
END;

CREATE TRIGGER recipes_produces_kind_update
BEFORE UPDATE OF produces_id ON recipes
WHEN NEW.produces_id IS NOT NULL
 AND (SELECT kind FROM vocab_entries WHERE id = NEW.produces_id) IS NOT 'ingredient'
BEGIN
    SELECT RAISE(ABORT, 'recipes.produces_id must reference an ingredient');
END;

-- What a recipe needs -------------------------------------------------------
-- One row per (recipe, requirement alternative). Rows sharing req_key are one requirement satisfied by
-- any of them (an OR-group). Excluded: optional lines, and any requirement where one alternative is a
-- staple (ingredients) or basic (equipment), since that requirement is always met.
-- kind 'ingredient' | 'equipment': entry_id is the vocabulary entry; NULL means an unresolved line.
-- kind 'recipe': sub_recipe_id is the recipe used as an ingredient (never part of an OR-group).
DROP VIEW recipe_requirements;

CREATE VIEW recipe_requirements AS
WITH
alternatives AS (
    SELECT r.recipe_id,
           CASE WHEN rl.sub_recipe_id IS NULL THEN 'ingredient' ELSE 'recipe' END AS kind,
           CASE WHEN rl.sub_recipe_id IS NULL THEN 'i:' ELSE 'r:' END
               || COALESCE('g' || rl.alt_group, 'l' || rl.id) AS req_key,
           rl.ingredient_id AS entry_id,
           rl.sub_recipe_id,
           COALESCE(ii.is_staple, 0) AS always_met
    FROM raw_lines AS r
    JOIN recipe_lines AS rl ON rl.raw_line_id = r.id
    LEFT JOIN ingredient_info AS ii ON ii.entry_id = rl.ingredient_id
    WHERE rl.is_optional = 0
    UNION ALL
    SELECT re.recipe_id,
           'equipment',
           'e:' || COALESCE('g' || re.alt_group, 'l' || re.id),
           re.equipment_id,
           NULL,
           COALESCE(ei.is_basic, 0)
    FROM recipe_equipment AS re
    LEFT JOIN equipment_info AS ei ON ei.entry_id = re.equipment_id
    WHERE re.is_optional = 0
)
SELECT DISTINCT a.recipe_id, a.kind, a.req_key, a.entry_id, a.sub_recipe_id
FROM alternatives AS a
WHERE NOT EXISTS (
    SELECT 1 FROM alternatives AS b
    WHERE b.recipe_id = a.recipe_id AND b.req_key = a.req_key AND b.always_met = 1
);
