from __future__ import annotations

import email as _email
import email.policy
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document
from ..base import ParserPlugin
from ..registry import register_parser

_REPLY_CHAIN_RE = re.compile(r"^>|^On .{10,}\s+wrote:\s*$", re.MULTILINE)
_BOUNDARY_RE = re.compile(r"^-{5,}Original Message-{5,}|^_{20,}", re.MULTILINE | re.IGNORECASE)


def _decode_payload(part) -> str | None:
    try:
        raw = part.get_payload(decode=True)
        if not raw:
            return None
        charset = part.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
    except Exception:
        logger.debug("Failed to decode MIME part payload", exc_info=True)
        return None


def _extract_text(msg) -> str:
    """Walk MIME tree, prefer text/plain, fall back to text/html stripped."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in msg.walk():
        ct = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        if ct == "text/plain":
            text = _decode_payload(part)
            if text:
                plain_parts.append(text)
        elif ct == "text/html":
            text = _decode_payload(part)
            if text:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(text, "html.parser")
                    for tag in soup(["script", "style"]):
                        tag.decompose()
                    html_parts.append(soup.get_text(separator="\n"))
                except ImportError:
                    html_parts.append(text)

    return "\n\n".join(plain_parts) or "\n\n".join(html_parts)


def _clean_body(text: str) -> list[str]:
    """Split into paragraphs, drop reply-chain lines and forwarding banners."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Truncate at common reply-chain markers
    for m in _BOUNDARY_RE.finditer(text):
        text = text[: m.start()]
        break

    paras = []
    for chunk in text.split("\n\n"):
        lines = []
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(">"):
                continue
            if re.match(r"^On .{10,}wrote:\s*$", stripped):
                continue
            lines.append(stripped)
        para = " ".join(lines).strip()
        if para and len(para) >= 4:
            paras.append(para)
    return paras


class EmlParser(ParserPlugin):
    name = "eml_parser"
    supported_types = ["eml"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        path = Path(ctx.source)
        try:
            raw = path.read_bytes()
            msg = _email.message_from_bytes(raw, policy=email.policy.compat32)
        except Exception as e:
            ctx.error("EML_PARSE_ERROR", str(e))
            return ctx

        subject = str(msg.get("Subject", "")).strip()
        sender  = str(msg.get("From", "")).strip()
        to      = str(msg.get("To", "")).strip()
        cc      = str(msg.get("Cc", "")).strip()
        date    = str(msg.get("Date", "")).strip()

        blocks: list[Block] = []
        idx = 0

        if subject:
            blocks.append(Block(type=BlockType.HEADING, content=subject, level=1, index=idx))
            idx += 1

        meta_parts = []
        if sender:
            meta_parts.append(f"From: {sender}")
        if to:
            meta_parts.append(f"To: {to}")
        if cc:
            meta_parts.append(f"CC: {cc}")
        if date:
            meta_parts.append(f"Date: {date}")
        if meta_parts:
            blocks.append(Block(type=BlockType.METADATA, content=" | ".join(meta_parts), index=idx))
            idx += 1

        body_text = _extract_text(msg)
        for para in _clean_body(body_text):
            blocks.append(Block(type=BlockType.PARAGRAPH, content=para, index=idx))
            idx += 1

        # List attachments
        attachments: list[str] = [
            name
            for p in msg.walk()
            if p.get_content_disposition() == "attachment"
            for name in (p.get_filename(),)
            if name is not None
        ]
        if attachments:
            blocks.append(Block(
                type=BlockType.METADATA,
                content=f"Attachments ({len(attachments)}): {', '.join(attachments)}",
                index=idx,
            ))
            idx += 1

        ctx.document = Document(
            source=str(path),
            file_type="eml",
            title=subject or path.stem,
            author=sender or None,
            pages=1,
            blocks=blocks,
            metadata={"attachments": len(attachments)},
        ).compute_id()
        return ctx


register_parser("eml", EmlParser)
