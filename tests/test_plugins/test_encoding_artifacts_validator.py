"""Tests for EncodingArtifactsValidator (W_ENCODING_ARTIFACTS, Phase 3).

Trigger A — XML tag residue:
    regex:      r'</?(?:pt|font|span|div|tspan)\\d+[^>]*>?'
    fires when: xml_fragment_count >= 3

Trigger C — Mojibake density:
    metric:     count("�") / total_chars
    fires when: mojibake_density >= 0.005

The two triggers OR together. Skip guards (file type, OCR_REQUIRED,
tiny-doc) apply to both.

Regression cases:
  - Trigger A positive (3+ numbered-tag fragments) → fires
  - Trigger A borderline (2 fragments) → silent
  - Trigger A negative on legit HTML in code block (``</span>`` etc.) → silent
  - Trigger C positive (mojibake density >= 0.5%) → fires
  - Trigger C negative (density below 0.5%) → silent
  - File-type guard, OCR_REQUIRED guard, tiny-doc guard

Design origin: user-confirmed pick from
``docs/calibration/PHASE_3_DETECTION.md`` §3.2 (Candidates A + C jointly).
"""
from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.models.validation import Severity, ValidationIssue
from aksharamd.plugins.validators.encoding_artifacts import (
    XML_FRAGMENT_RE,
    EncodingArtifactsValidator,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(
    blocks: list[Block],
    file_type: str = "pdf",
    pages: int = 1,
) -> CompilationContext:
    doc = Document(
        source="test." + file_type,
        file_type=file_type,
        pages=pages,
        blocks=blocks,
        metadata={},
    )
    doc.compute_id()
    ctx = CompilationContext(source="test." + file_type, output_dir="/tmp/out")
    ctx.document = doc
    return ctx


def _warning_codes(ctx: CompilationContext) -> set[str]:
    return {
        getattr(i, "code", None)
        for i in ctx.validation.issues
        if i.severity.value == "warning"
    }


def _padding_prose(n_lines: int) -> str:
    return "\n".join(
        f"Prose line number {i} with several words of ordinary content."
        for i in range(n_lines)
    )


def _de_analogue_content(n_fragments: int) -> str:
    """Return content with ``n_fragments`` numbered-tag residue fragments.

    Models ``text_dense__de``: PDF-to-XML pipeline residue where numbered
    tags leak into the extracted output.
    """
    parts = []
    for i in range(n_fragments):
        # Each unit is one `</ptN>` (numbered close tag) — one match per unit.
        parts.append(f"Text before </pt{100 + i}> more text ")
    return " ".join(parts)


# ── regex sanity ──────────────────────────────────────────────────────────────


class TestXmlFragmentRegex:
    def test_numbered_close_tag_matches(self) -> None:
        assert XML_FRAGMENT_RE.search("</pt192>")
        assert XML_FRAGMENT_RE.search("</font17>")
        assert XML_FRAGMENT_RE.search("<tspan42>")

    def test_de_style_unclosed_fragment_matches(self) -> None:
        """`</pt192><pt193` — the de-doc failure signature."""
        matches = XML_FRAGMENT_RE.findall("</pt192><pt193")
        assert len(matches) >= 1

    def test_plain_html_close_tag_does_not_match(self) -> None:
        """Legitimate HTML close tags (no numeric suffix) must not match.

        This is the FP guard: a code block containing ``</span>`` must not
        fire the warning.
        """
        assert not XML_FRAGMENT_RE.search("</span>")
        assert not XML_FRAGMENT_RE.search("</div>")
        assert not XML_FRAGMENT_RE.search("</font>")
        assert not XML_FRAGMENT_RE.search("</pt>")  # tag but no digits

    def test_plain_html_open_tag_does_not_match(self) -> None:
        assert not XML_FRAGMENT_RE.search("<span>")
        assert not XML_FRAGMENT_RE.search("<div>")
        assert not XML_FRAGMENT_RE.search('<div class="container">')
        assert not XML_FRAGMENT_RE.search("<span style='x'>")

    def test_prose_without_tags_does_not_match(self) -> None:
        assert not XML_FRAGMENT_RE.search("Ordinary text with no tags.")
        assert not XML_FRAGMENT_RE.search("Numbers like 192 are fine.")


# ── Trigger A: XML tag residue ────────────────────────────────────────────────


class TestEncodingArtifactsXmlTrigger:
    def test_three_fragments_fires(self) -> None:
        """de analogue with 3 numbered-tag fragments → should fire."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx), (
            "Expected W_ENCODING_ARTIFACTS on 3+ XML-tag residue fragments"
        )
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("warned") is True
        assert diag.get("xml_fragment_count") >= 3
        assert "xml_fragment" in diag.get("fired_triggers", [])

    def test_two_fragments_silent(self) -> None:
        """Borderline: only 2 fragments — below the 3-match threshold."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(2),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("xml_fragment_count") == 2

    def test_legit_html_in_code_block_silent(self) -> None:
        """HTML documentation content with ``</span>`` etc. must NOT fire.

        This is the key FP-guard test: a code block discussing HTML tags is
        a legitimate content case that must stay silent.
        """
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        code_content = (
            "Here is an HTML example: "
            "<div class='container'><span>Hello</span></div> "
            "and another: <p>World</p> "
            "and closing: </span></div></p></font> "
            "and yet more: <span class='x'></span>"
        )
        code = Block(
            type=BlockType.PARAGRAPH,
            content=code_content,
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, code])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx), (
            "Legit HTML fragments without numeric suffixes must not fire "
            "the XML-residue trigger"
        )
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("xml_fragment_count") == 0


# ── Trigger C: Mojibake density ───────────────────────────────────────────────


class TestEncodingArtifactsMojibakeTrigger:
    def test_high_mojibake_density_fires(self) -> None:
        """Density >= 0.5 percent replacement chars → should fire.

        Enough replacement characters in the mojibake block that the
        whole-document density crosses 0.5 percent even after averaging
        with 20 lines of clean prose padding (~1200 chars).
        """
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        # Repeat a dense mojibake run so absolute count is high enough
        # to push whole-document density well above 0.5 percent.
        heavy_run = " ".join(["� � � � � � � � � �"] * 5)  # ~50 chars
        mojibake = Block(
            type=BlockType.PARAGRAPH,
            content=(
                "German patent doc: die Vorrichtung umfasst die "
                "wesentlichen Bauteile die zur Funktion notwendig sind. "
                + heavy_run
            ),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, mojibake])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx), (
            "Expected W_ENCODING_ARTIFACTS on high mojibake density"
        )
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("warned") is True
        assert diag.get("mojibake_density") >= 0.005
        assert "mojibake_density" in diag.get("fired_triggers", [])

    def test_low_mojibake_density_silent(self) -> None:
        """A single stray replacement char in an otherwise clean doc must not fire."""
        # ~1000 chars of clean prose with just 1 replacement char → 0.1% density.
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(30) + "\nOne stray � char here.",
            page=1,
            index=0,
            metadata={},
        )
        ctx = _make_ctx([prose])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("mojibake_density") < 0.005

    def test_no_mojibake_silent(self) -> None:
        """Clean doc without any replacement chars must not fire."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(30),
            page=1,
            index=0,
            metadata={},
        )
        ctx = _make_ctx([prose])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("mojibake_count") == 0
        assert diag.get("mojibake_density") == 0.0


# ── Both triggers fire together ───────────────────────────────────────────────


class TestEncodingArtifactsCombined:
    def test_both_triggers_recorded_in_fired_triggers(self) -> None:
        """When both triggers fire, both must appear in fired_triggers.

        The combined block carries enough replacement characters to
        push the whole-document mojibake density above 0.5 percent even
        with 20 lines of clean prose padding, and enough numbered
        pt-tag residue to cross the XML-fragment threshold of 3.
        """
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        # Combine XML residue + heavy mojibake in the same block. The
        # mojibake share must be high enough that when averaged with the
        # ~1200 chars of clean prose padding, the density still crosses
        # 0.005 (0.5 percent). Use 30+ replacement chars to be safe.
        heavy_mojibake_repeats = " ".join(["� � � � � � � � � �"] * 3)
        combined = Block(
            type=BlockType.PARAGRAPH,
            content=(
                "Die Vorrichtung </pt192> mit der Halterung </pt193> "
                "und dem Anschluss </pt194> für die Steuerung. "
                + heavy_mojibake_repeats
            ),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, combined])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert "xml_fragment" in diag.get("fired_triggers", [])
        assert "mojibake_density" in diag.get("fired_triggers", [])


# ── Guard 1: file-type eligibility ────────────────────────────────────────────


class TestEncodingArtifactsFileTypeGuard:
    def test_txt_file_not_evaluated(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue], file_type="txt")
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)
        assert "encoding_artifacts_diagnostics" not in ctx.document.metadata

    def test_markdown_file_not_evaluated(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue], file_type="md")
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)

    def test_docx_eligible(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue], file_type="docx")
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx)

    def test_html_eligible(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue], file_type="html")
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx)


# ── Guard 2: OCR_REQUIRED suppression ─────────────────────────────────────────


class TestEncodingArtifactsOcrSuppression:
    def test_ocr_required_suppresses_xml_trigger(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue])
        ctx.add_issue(
            ValidationIssue(
                severity=Severity.WARNING,
                code="OCR_REQUIRED",
                message="scanned pages, OCR not installed",
            ),
        )
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("warned") is False
        assert "OCR_REQUIRED" in (diag.get("suppressed_reason") or "")

    def test_ocr_required_suppresses_mojibake_trigger(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=(
                "Die � Vorrichtung � mit der � Halterung � und dem "
                "� Anschluss � für die � Steuerung der Bauteile."
            ),
            page=1,
            index=0,
            metadata={},
        )
        # Pad up to 20+ lines
        pad = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, pad])
        ctx.add_issue(
            ValidationIssue(
                severity=Severity.WARNING,
                code="OCR_REQUIRED",
                message="scanned pages, OCR not installed",
            ),
        )
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)


# ── Guard 3: tiny-doc guard ───────────────────────────────────────────────────


class TestEncodingArtifactsTinyDocGuard:
    def test_tiny_doc_not_evaluated(self) -> None:
        """5 residue fragments on a 3-line doc must NOT fire — tiny-doc guard."""
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(5),
            page=1,
            index=0,
            metadata={},
        )
        ctx = _make_ctx([residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("warned") is False
        assert "nonempty" in (diag.get("suppressed_reason") or "")

    def test_boundary_at_twenty_lines_evaluated(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx)


# ── Contract / stability ──────────────────────────────────────────────────────


class TestEncodingArtifactsContract:
    def test_no_document_is_noop(self) -> None:
        ctx = CompilationContext(source="none.pdf", output_dir="/tmp/out")
        ctx.document = None
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)

    def test_no_output_mutation(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue_content = _de_analogue_content(3)
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=residue_content,
            page=1,
            index=1,
            metadata={},
        )
        original_prose = prose.content
        ctx = _make_ctx([prose, residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert ctx.document.blocks[0].content == original_prose
        assert ctx.document.blocks[1].content == residue_content

    def test_no_score_cap_applied(self) -> None:
        """Detection-only signal — must not attach a readiness cap in v1."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None

    def test_warning_code_is_stable(self) -> None:
        """The warning code string is public API and must not be renamed."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx)

    def test_warning_maturity_is_candidate(self) -> None:
        v = EncodingArtifactsValidator()
        assert v.warning_maturity == "candidate"

    def test_diagnostics_include_maturity(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        residue = Block(
            type=BlockType.PARAGRAPH,
            content=_de_analogue_content(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, residue])
        ctx = EncodingArtifactsValidator().execute(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("warning_maturity") == "candidate"
