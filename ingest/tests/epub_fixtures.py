"""Build small synthetic EPUBs for tests: original recipes laid out like a converted Kindle book."""

import zipfile
from pathlib import Path

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""


def recipe_html(anchor: str, number: int, title: str, ingredients: list[str], steps: list[str], numbered: bool = False) -> str:
    """One recipe in the style of the book: anchor span, numbered title, blurb, labels, bulleted lines."""
    if numbered:
        items = "".join(f'<li value="{i}">{line}</li>' for i, line in enumerate(ingredients, 1))
        ingredient_block = f"<ol>{items}</ol>"
    else:
        ingredient_block = "".join(f"<p>• {line}</p>" for line in ingredients)
    step_block = "".join(f"<p>• {step}</p>" for step in steps)
    return (
        f'<div><ol><li style="list-style: none"><span id="{anchor}"></span></li>'
        f'<li value="{number}"><font><b>{title}</b></font></li></ol></div>'
        f'<p><i>"A short blurb."</i><br/><i>Serving: 2 | Prep: 5m | Ready in: 15m</i></p>'
        f"<p><b>Ingredients</b></p>{ingredient_block}"
        f"<p><b>Direction</b></p>{step_block}"
        f"<p><b>Nutrition Information</b></p><p>• Calories: 100 calories;</p>"
    )


def make_epub(
    path: Path,
    documents: dict[str, str],
    toc: list[tuple[str, str]],
    *,
    title: str = "A Synthetic Cookbook",
    identifiers: str = '<dc:identifier id="bookid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>',
) -> Path:
    manifest = "".join(f'<item href="{name}" id="d{i}" media-type="application/xhtml+xml"/>' for i, name in enumerate(documents))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(documents)))
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    {identifiers}
    <dc:title>{title}</dc:title><dc:creator>Test Author</dc:creator><dc:language>en</dc:language>
  </metadata>
  <manifest>{manifest}<item href="toc.ncx" id="ncx" media-type="application/x-dtbncx+xml"/></manifest>
  <spine toc="ncx">{spine}</spine>
</package>"""
    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{label}</text></navLabel><content src="{src}"/></navPoint>'
        for i, (label, src) in enumerate(toc, 1)
    )
    ncx = f'<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>{points}</navMap></ncx>'

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/toc.ncx", ncx)
        for name, body in documents.items():
            archive.writestr(f"OEBPS/{name}", f"<html><head><title>x</title><style>p {{}}</style></head><body>{body}</body></html>")
    return path


def sample_book(path: Path, recipes: int = 5, **kwargs) -> Path:
    """Front matter, `recipes` recipes split over two documents, an ambiguous entry and a closing page."""
    first, second = recipes // 2 + 1, recipes - recipes // 2 - 1
    docs = {
        "part1.html": '<span id="intro"></span><p>Welcome!</p><p>Click HERE for a bonus.</p>',
        "part2.html": "".join(
            recipe_html(f"r{n}", n, f"Recipe {n}", [f"{n} cup rice", "salt to taste"], ["Cook the rice."], numbered=n % 2 == 0)
            for n in range(1, first + 1)
        ),
        "part3.html": "".join(
            recipe_html(f"r{n}", n, f"Recipe {n}", ["2 eggs", "1 tsp&nbsp;pepper"], ["Whisk.", "Heat the pan."])
            for n in range(first + 1, first + second + 1)
        )
        + '<span id="tips"></span><p><b>Ingredients</b></p><p>See the pantry list.</p>'
        + '<span id="end"></span><p><b>Conclusion</b></p><p>Thank you!</p>',
    }
    toc = [("Introduction", "part1.html#intro")]
    toc += [(f"{n}.&#160;&#160;&#160;Recipe {n}", f"{'part2' if n <= first else 'part3'}.html#r{n}") for n in range(1, first + second + 1)]
    toc += [("Tips", "part3.html#tips"), ("Conclusion", "part3.html#end")]
    return make_epub(path, docs, toc, **kwargs)
