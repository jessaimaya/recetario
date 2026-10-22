"""The stages of the ingest pipeline that exist so far: text, then segment."""

import hashlib
import json
import re
from pathlib import Path

from dataclasses import asdict

from recetario_ingest import segment as segment_module
from recetario_ingest import classify, epub, extraction, line_split, lines, localize, pdf, resolver, segment, terms
from recetario_ingest.vocabulary import Vocabulary
from recetario_ingest.pipeline import Context, Stage, StageError, StageOutput, Unit


def _hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class TextStage(Stage):
    """Book level: read the EPUB into one text, with the anchors and table of contents needed to cut it up."""

    name = "text"
    version = "1"

    def __init__(self, path: Path) -> None:
        self._path = path

    def units(self, ctx: Context) -> list[Unit]:
        return [Unit(key=None, input_hash=ctx.source.file_hash)]

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        if self._path.suffix.lower() == ".pdf":
            book = pdf.read_pdf(self._path)
            return StageOutput({"format": "pdf", "pages": book.pages, "text": "\f".join(book.pages), "anchors": {}, "documents": {}, "toc": []})
        book = epub.read_epub(self._path)
        return StageOutput(
            {
                "text": book.book_text.text,
                "anchors": book.book_text.anchors,
                "documents": book.book_text.documents,
                "toc": [{"title": entry.title, "src": entry.src} for entry in book.toc],
            }
        )


class SegmentStage(Stage):
    """Recipe level: one unit per table-of-contents entry, classified as a recipe or not."""

    name = "segment"
    version = "1"

    def units(self, ctx: Context) -> list[Unit]:
        stored = ctx.store.read("text", None)
        if stored.get("format") == "pdf":
            return self._units(segment.slice_pages(stored["pages"]), "pages")
        if not stored["toc"]:
            raise StageError("the book has no table of contents; segmenting without one isn't implemented yet")
        book = epub.BookText(stored["text"], stored["anchors"], stored["documents"])
        toc = [epub.TocEntry(**entry) for entry in stored["toc"]]
        return self._units(segment.slice_book(book, toc), "toc")

    def _units(self, slices, method: str) -> list[Unit]:
        units, seen = [], set()
        for slice_ in slices:
            key = _recipe_key(slice_.entry.src)
            while key in seen:  # two entries can point at the same anchor
                key += "-again"
            seen.add(key)
            input_hash = _hash(slice_.entry.src, slice_.entry.title, slice_.text or "")
            units.append(Unit(key=key, input_hash=input_hash, payload=(slice_, method)))
        return units

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        slice_, method = unit.payload
        kind, confidence = segment.classify(slice_.text)
        return StageOutput(
            {
                "recipe_key": unit.key,
                "locator": slice_.entry.src,
                "toc_title": slice_.entry.title,
                "title": segment.clean_title(slice_.entry.title),
                "kind": kind,
                "confidence": confidence,
                "method": method,
                "text": slice_.text or "",
            }
        )


def _recipe_key(src: str) -> str:
    """The locator as a name that is safe in a file name: text00000.html#fp1 -> text00000.html-fp1."""
    return src.replace("#", "-").replace("/", "_")


class ExtractStage(Stage):
    """Recipe level: ask the model for the structured recipe, check it, retry once on errors."""

    name = "extract"
    version = "1"
    prompt_id = extraction.PROMPT_ID

    def units(self, ctx: Context) -> list[Unit]:
        if ctx.llm is None:
            raise StageError("no model is configured")
        return [
            Unit(key=segment["recipe_key"], input_hash=_hash(segment["text"]), payload=segment)
            for segment in ctx.store.read_all("segment")
            if segment["kind"] == "recipe"
        ]

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        segment = unit.payload
        result = extraction.extract_recipe(ctx.llm, segment["text"], ctx.prompts.get(self.prompt_id).version)
        if result.extracted and result.extracted.get("title"):
            result.extracted["title"] = segment_module.reconcile_title(result.extracted["title"], segment["title"], authoritative=segment["method"] == "pages")
        return StageOutput(
            {
                "recipe_key": unit.key,
                "locator": segment["locator"],
                "status": result.status,
                "attempts": result.attempts,
                "extracted": result.extracted,
                "issues": [asdict(issue) for issue in result.issues],
            },
            result.usage,
        )


class LinesStage(Stage):
    """Ingredient lines the rules can't read ("salt and pepper"): one unit per unique line, the model names its parts."""

    name = "lines"
    version = "1"
    prompt_id = line_split.PROMPT_ID

    def units(self, ctx: Context) -> list[Unit]:
        if ctx.llm is None:
            raise StageError("no model is configured")
        unknown = {}
        for item in ctx.store.read_all("extract"):
            for raw in (item["extracted"] or {}).get("ingredient_lines", []):
                parsed = lines.parse_line(raw)
                if parsed.needs_model:
                    unknown[raw] = parsed
        return [Unit(key=_hash(raw)[:16], input_hash=_hash(raw), payload=parsed) for raw, parsed in sorted(unknown.items())]

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        parsed = unit.payload
        version = ctx.prompts.get(self.prompt_id).version
        parts, usage = line_split.split_line(ctx.llm, version, parsed.raw, parsed.name)
        return StageOutput({"raw": parsed.raw, "reason": parsed.needs_model, "parts": parts}, usage)


def split_names(store) -> dict[str, list[str]]:
    """raw ingredient line -> the names the model found in it, from the `lines` artifacts."""
    return {item["raw"]: [part["name"] for part in item["parts"]] for item in store.read_all("lines")}


def split_parts(store) -> dict[str, list[dict]]:
    """raw ingredient line -> its parts (name, relation), from the `lines` artifacts."""
    return {item["raw"]: item["parts"] for item in store.read_all("lines")}


class ResolveStage(Stage):
    """Terms of the book (ingredients, equipment names and attribute keys): one unit per unique term.

    The vocabulary tables are the source of truth, so this stage writes auto-accepted aliases and open proposals
    to the database itself; its artifacts only record what happened, which also spares repeated model calls.
    """

    name = "resolve"
    version = "1"
    prompt_id = resolver.PROMPT_ID

    def units(self, ctx: Context) -> list[Unit]:
        if ctx.llm is None or ctx.conn is None:
            raise StageError("no model is configured")
        found = terms.collect_terms(ctx.store.read_all("extract"), split_names(ctx.store))
        return [
            Unit(key=f"{kind}-{term}", input_hash=_hash(kind, term), payload=(kind, term, examples))
            for (kind, term), examples in found.items()
        ]

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        kind, term, examples = unit.payload
        version = ctx.prompts.get(self.prompt_id).version
        resolution = resolver.Resolver(ctx.conn, ctx.llm, version, ctx.source.id, lock=ctx.db_lock).resolve(kind, term, examples)
        return StageOutput(resolution.as_json(), resolution.usage)


class LocalizeStage(Stage):
    """Recipe level: translate to es-MX with the resolved names as hints.

    The input includes those names, so a recipe is translated again once a review pass settles more of its terms
    (re-run with `--force-stage localize` after reviewing).
    """

    name = "localize"
    version = "1"
    prompt_id = localize.PROMPT_ID

    def units(self, ctx: Context) -> list[Unit]:
        if ctx.llm is None or ctx.conn is None:
            raise StageError("no model is configured")
        vocabulary = {kind: Vocabulary(ctx.conn, kind) for kind in ("ingredient", "equipment")}
        splits = split_parts(ctx.store)
        units = []
        for item in ctx.store.read_all("extract"):
            if not item["extracted"]:
                continue
            recipe = recipe_for_translation(item["extracted"], vocabulary, splits)
            units.append(Unit(key=item["recipe_key"], input_hash=_hash(json.dumps(recipe, sort_keys=True, ensure_ascii=False)), payload=recipe))
        return units

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        version = ctx.prompts.get(self.prompt_id).version
        result = localize.localize_recipe(ctx.llm, version, unit.payload)
        return StageOutput(
            {
                "recipe_key": unit.key, "status": result.status, "attempts": result.attempts, "prompt_version": version,
                **(result.translated or {}), "issues": [asdict(issue) for issue in result.issues],
            },
            result.usage,
        )


class ClassifyStage(Stage):
    """Recipe level, last on purpose: forcing it re-runs nothing else. One course per recipe from its title and ingredients.

    A recipe the model can't place gets no course and the book still loads; `--force-stage classify` tries again.
    """

    name = "classify"
    version = "1"
    prompt_id = classify.PROMPT_ID

    def units(self, ctx: Context) -> list[Unit]:
        if ctx.llm is None:
            raise StageError("no model is configured")
        units = []
        for item in ctx.store.read_all("extract"):
            if not item["extracted"]:
                continue
            text = classify.recipe_text(item["extracted"])
            units.append(Unit(key=item["recipe_key"], input_hash=_hash(text), payload=text))
        return units

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        version = ctx.prompts.get(self.prompt_id).version
        course, usage, problem = classify.classify_recipe(ctx.llm, version, unit.payload)
        data = {"recipe_key": unit.key, "course": course, "prompt_version": version, "problem": problem}
        return StageOutput(data, usage) if usage is not None else StageOutput(data)


def recipe_for_translation(data: dict, vocabulary: dict[str, Vocabulary], splits: dict[str, list[dict]]) -> dict:
    """The extraction plus what the vocabulary knows: es-MX names per ingredient line, metric amounts, equipment names."""
    ingredient_lines = []
    for text in data["ingredient_lines"]:
        parsed = lines.parse_line(text)
        parts = splits.get(text, []) if parsed.needs_model else [{"name": parsed.name}]
        names = []
        for part in parts:
            entry = vocabulary["ingredient"].exact(terms.normalize_term("ingredient", part["name"])) if part["name"] else None
            if entry and entry.label_es_mx and entry.label_es_mx not in names:
                names.append(entry.label_es_mx)
        amount = localize.metric_amount(parsed.qty, parsed.unit)
        container = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(oz|lb)s?", parsed.container or "")
        if container:
            amount = ((amount + "; ") if amount else "") + "container " + localize.metric_amount(float(container[1]), container[2])
        ingredient_lines.append({"text": text, "names": names, "amount": amount})
    equipment = {}
    for mention in data["equipment_mentions"]:
        entry = vocabulary["equipment"].exact(terms.normalize_term("equipment", mention["text"]))
        if entry and entry.label_es_mx:
            equipment[entry.name_en] = entry.label_es_mx
    return {"title": data["title"], "description": data.get("description") or "", "steps": data["steps"], "ingredient_lines": ingredient_lines, "equipment": equipment}
