from __future__ import annotations

from pathlib import Path

import chardet
from bs4 import BeautifulSoup, Tag

from ...context import CompilationContext
from ...models.asset import Asset
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser
from .html import _walk


def _read_file(path: Path) -> str:
    raw = path.read_bytes()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def _rst_to_html(text: str) -> str:
    from docutils.core import publish_string  # type: ignore[import-untyped]
    from docutils.utils import SystemMessage  # type: ignore[import-untyped]
    try:
        from docutils.writers import html5_polyglot  # type: ignore[import-untyped]
        html_bytes = publish_string(
            text,
            writer=html5_polyglot.Writer(),
            settings_overrides={
                "halt_level": 5,       # don't raise on warnings
                "report_level": 5,     # suppress docutils stderr noise
                "syntax_highlight": "none",  # plain text in code blocks
            },
        )
        return html_bytes.decode("utf-8")
    except SystemMessage:
        return f"<html><body><pre>{text}</pre></body></html>"


class RSTParser(ParserPlugin):
    name = "rst_parser"
    supported_types = ["rst"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        text = _read_file(path)

        html = _rst_to_html(text)
        soup = BeautifulSoup(html, "html.parser")

        title: str | None = None
        title_tag = soup.find(["h1", "title"])
        if title_tag:
            title = title_tag.get_text(strip=True)

        blocks: list = []
        assets: list[Asset] = []
        idx = [0]

        _body = soup.find("main") or soup.find("body") or soup
        body: Tag = _body if isinstance(_body, Tag) else soup
        _walk(body, blocks, assets, idx, source_path=path)

        doc = Document(
            source=str(path),
            file_type="rst",
            title=title or path.stem,
            pages=1,
            blocks=blocks,
            assets=assets,
        )
        doc.compute_id()
        ctx.document = doc
        return ctx


register_parser("rst", RSTParser)
