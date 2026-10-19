"""Text normalization shared by aliases and, later, the vocabulary resolver."""

import unicodedata


def normalize(text: str) -> str:
    """Casefold, drop accents and collapse whitespace: "  Jalapeño  Pepper" -> "jalapeno pepper"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.split())
