from __future__ import annotations
from pathlib import Path

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document


class MsgParser(ParserPlugin):
    name = "msg_parser"
    supported_types = ["msg"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import extract_msg

        path = Path(ctx.source)
        try:
            msg = extract_msg.openMsg(str(path))
        except Exception as e:
            ctx.error("MSG_PARSE_ERROR", str(e))
            return ctx

        subject  = (msg.subject or "").strip()
        sender   = (msg.sender or "").strip()
        to       = (msg.to or "").strip()
        cc       = (msg.cc or "").strip()
        date_str = str(msg.date or "")
        body     = (msg.body or "").strip()

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
        if date_str:
            meta_parts.append(f"Date: {date_str}")
        if meta_parts:
            blocks.append(Block(type=BlockType.METADATA, content=" | ".join(meta_parts), index=idx))
            idx += 1

        if body:
            # Split on double newlines into paragraphs; strip reply-chain markers
            for para in body.split("\n\n"):
                text = para.strip()
                if not text or len(text) < 3:
                    continue
                # Skip common reply-chain noise
                if text.startswith(">") or text.startswith("On ") and "wrote:" in text:
                    continue
                blocks.append(Block(type=BlockType.PARAGRAPH, content=text, index=idx))
                idx += 1

        # Attachments as metadata
        attachments = getattr(msg, "attachments", []) or []
        if attachments:
            att_names = ", ".join(
                getattr(a, "longFilename", None) or getattr(a, "shortFilename", "attachment")
                for a in attachments
            )
            blocks.append(Block(
                type=BlockType.METADATA,
                content=f"Attachments ({len(attachments)}): {att_names}",
                index=idx,
            ))
            idx += 1

        try:
            msg.close()
        except Exception:
            pass

        ctx.document = Document(
            source=str(path),
            file_type="msg",
            title=subject or path.stem,
            author=sender or None,
            pages=1,
            blocks=blocks,
            metadata={"attachments": len(attachments)},
        ).compute_id()
        return ctx


register_parser("msg", MsgParser)
