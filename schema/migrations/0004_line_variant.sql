-- The form an ingredient line asks for ("canned", "frozen", "boiling"), kept apart from the ingredient it resolves to.
-- Matching ignores it: a recipe's requirement is still the ingredient. Free text, null when the line names no form.
ALTER TABLE recipe_lines ADD COLUMN variant TEXT;
