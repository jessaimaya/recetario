-- Ingredients that stand in for one another ("cooking oil": olive, canola, coconut...). Entries sharing a group are
-- interchangeable in a recipe; a null group means no known substitute. Set from the seed file on every load.
ALTER TABLE vocab_entries ADD COLUMN substitute_group TEXT;
