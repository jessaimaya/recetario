-- Pairs of recipes a person has looked at, so the duplicate check never asks about them again.
-- 'duplicate' also sets recipes.duplicate_of on the later recipe; nothing is ever deleted automatically.
CREATE TABLE duplicate_reviews (
    recipe_id  INTEGER NOT NULL REFERENCES recipes (id) ON DELETE CASCADE,
    other_id   INTEGER NOT NULL REFERENCES recipes (id) ON DELETE CASCADE,
    decision   TEXT NOT NULL CHECK (decision IN ('duplicate', 'distinct')),
    decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (recipe_id, other_id),
    CHECK (recipe_id < other_id)
) STRICT;
