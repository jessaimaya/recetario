-- What kind of dish a recipe is, so it can be filtered ("something for dessert"). One value per recipe; null until
-- classified. Set by the ingest classify stage.
ALTER TABLE recipes ADD COLUMN course TEXT CHECK (
    course IN ('plato_fuerte', 'guarnicion', 'postre', 'aderezo', 'desayuno', 'bebida', 'entrada', 'snack')
);
