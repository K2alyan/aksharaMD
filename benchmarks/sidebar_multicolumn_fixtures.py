"""Synthetic multicolumn+sidebar fixture generator (Issue #50).

Analysis-only. **No production code changes.** Constructs deterministic
in-memory block sequences that mimic real page geometries so the
cluster-exclusion prototype can be exercised without needing PDF bytes.

Every fixture is a list of ``{index, type, x0, y0, chars, words, content}``
dicts plus a ``page_width`` and ``page_height``. This matches the shape
that ``sidebar_multicolumn_baseline_signals.py`` consumes and mirrors
what ``document.json`` provides for a compiled real page.

Fixtures provided:

- ``sidebar_only_page()`` — recreates the strikeUnderline geometry
  (1-column body + right-margin markers). Baseline warns; cluster
  exclusion of the sidebar minority silences.
- ``true_three_column_page()`` — recreates a 3colpres-style page
  (small bottom-right callout + main body). Baseline warns; H6 keeps
  the warning; cluster exclusion of the callout would ALSO silence
  (evidence that even the true positive is being warned for the
  wrong structural reason on this parser).
- ``mixed_multicolumn_and_sidebar_page()`` — the fixture required by
  the phase spec: two genuine columns of body text PLUS a right-side
  sidebar. The baseline detector warns (correctly — genuine
  multicolumn). Blanket page-level suppression under H6 would
  incorrectly silence the entire warning. Cluster exclusion of the
  sidebar preserves the warning on the remaining two-column body.
- ``single_column_control()`` — clean single-column page. Baseline
  does not warn. Neither approach changes the decision.

All fixtures use PDF-point coordinates and a US-letter page.
"""
from __future__ import annotations

from typing import Any

_PAGE_W = 612.0
_PAGE_H = 792.0


def _blk(index: int, type_: str, x0: float, y0: float, content: str) -> dict[str, Any]:
    return {
        "index": index,
        "type": type_,
        "x0": x0,
        "y0": y0,
        "content": content,
        "chars": len(content),
        "words": len(content.split()),
    }


def _sort_by_y(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real compiled blocks arrive in reading order — which for most
    single-page fixtures is y-sorted. Reproduce that order so the
    baseline detector's ``transition_rate`` computation is faithful.
    Ties broken by x-position.
    """
    return sorted(blocks, key=lambda b: (b["y0"], b["x0"]))


def sidebar_only_page() -> dict[str, Any]:
    """Single-column body + right-margin marker sidebar. Mirrors the
    strikeUnderline geometry closely enough for the prototype to
    reproduce the block-level detector's warning.
    """
    body = [
        _blk(0, "paragraph", x0=66.0, y0=145.0,
             content=("Section one. " * 12).strip()),
        _blk(1, "paragraph", x0=66.0, y0=234.0,
             content=("Body paragraph with sentences. " * 32).strip()),
        _blk(2, "paragraph", x0=66.0, y0=432.0,
             content=("Continuing body. " * 30).strip()),
        _blk(3, "paragraph", x0=66.0, y0=629.0,
             content=("Final body paragraph. " * 8).strip()),
    ]
    # Right-margin revision markers — 1-word each, uniform x, spans page height.
    sidebar = [
        _blk(10, "heading", x0=404.0, y0=145.0, content="§1"),
        _blk(11, "heading", x0=404.0, y0=223.0, content="§2"),
        _blk(12, "heading", x0=404.0, y0=420.0, content="§3"),
        _blk(13, "heading", x0=404.0, y0=617.0, content="§4"),
    ]
    return {
        "name": "sidebar_only_page",
        "page_width": _PAGE_W,
        "page_height": _PAGE_H,
        "blocks": _sort_by_y(body + sidebar),
        "expected_baseline_warn": True,
        "expected_cluster_exclusion_warn": False,
        "expected_multicolumn": False,
        "expected_h6_matches": True,
        "notes": "single-column body + right-margin sidebar; genuine FP class",
    }


def true_three_column_page() -> dict[str, Any]:
    """Approximates 3colpres: three body columns plus a small
    bottom-right callout. The validator's largest-x0-gap clustering
    lumps the leftmost + middle body columns together, isolating only
    the callout in cluster 1.
    """
    body_left = [
        _blk(0, "paragraph", x0=57.0, y0=32.0, content=("Left column, sentence. " * 4).strip()),
        _blk(1, "heading", x0=67.0, y0=101.0, content=("Section header " * 2).strip()),
        _blk(2, "paragraph", x0=57.0, y0=255.0, content=("Left column body. " * 40).strip()),
        _blk(3, "paragraph", x0=57.0, y0=400.0, content=("More left column body. " * 60).strip()),
        _blk(4, "paragraph", x0=57.0, y0=603.0, content=("Even more left column body. " * 30).strip()),
        _blk(5, "paragraph", x0=57.0, y0=760.0, content=("Final left column body. " * 300).strip()),
    ]
    body_middle = [
        # x=233 — between left column and the biggest-gap boundary at ~321.
        # Middle column blocks lumped with the left cluster.
        _blk(6, "paragraph", x0=233.0, y0=651.0, content=("Middle column start. " * 8).strip()),
        _blk(7, "paragraph", x0=233.0, y0=699.0, content=("Middle column continues. " * 12).strip()),
        _blk(8, "paragraph", x0=145.0, y0=135.0, content=("Middle column top. " * 25).strip()),
    ]
    # Bottom-right callout — small isolated region, cluster 1
    callout = [
        _blk(20, "heading", x0=410.0, y0=687.0, content="Callout Heading"),
        _blk(21, "paragraph", x0=460.0, y0=760.0, content="Small caption text here."),
    ]
    return {
        "name": "true_three_column_page",
        "page_width": _PAGE_W,
        "page_height": _PAGE_H,
        "blocks": _sort_by_y(body_left + body_middle + callout),
        "expected_baseline_warn": True,
        # H6 does NOT match on this page (cov of the smaller cluster is
        # only ~0.09 — the callout is bottom-of-page, not full-height).
        # So cluster exclusion is NOT triggered and the baseline verdict
        # is preserved. This is exactly why H6 preserves 3colpres —
        # the callout is compact rather than tall.
        "expected_cluster_exclusion_warn": True,
        "expected_multicolumn": True,
        "expected_h6_matches": False,
        "notes": ("3colpres surrogate: parser lumps left+middle columns; "
                  "callout is cluster 1. Baseline warns because callout "
                  "blocks create y-sorted transitions. H6 does NOT match "
                  "because callout is compact (cov ≈ 0.09), so cluster "
                  "exclusion is not applied and the baseline verdict is "
                  "preserved. Documents that H6 preserves 3colpres via the "
                  "coverage gate, not via alternations."),
    }


def mixed_multicolumn_and_sidebar_page() -> dict[str, Any]:
    """The critical fixture: genuine two-column body AND a right-margin
    sidebar. Baseline correctly warns (two body columns interleave in
    y-sort). Blanket suppression under H6 (page-level rule) would
    incorrectly silence the whole warning. Cluster-exclusion + baseline
    recomputation must preserve the warning because after removing the
    sidebar, the two body columns still show the multicolumn signal.

    Design:
    - Left body column at x=60, blocks 0..7 alternating with right body
      column at x=310, blocks 0..7 by y. The parser's largest-x0-gap
      boundary sits between the right body column (x=310) and the sidebar
      (x=500), not between the two body columns — so cluster 0 = left
      body + right body, cluster 1 = sidebar only.
    - Sidebar at x=500, four uniform 1-word blocks spanning y=120..640.
    - After sidebar removal, the remaining 16 body blocks form a
      geometry with a big x-gap between x=60 and x=310 and alternating
      y-sorted transitions — which triggers the block-level warning.

    Note: producing a fixture where cluster-exclusion clearly preserves
    the warning while blanket-suppression fails is the whole point of
    this fixture. Both baseline signal fires must be strong enough that
    removing the tiny sidebar does not tip them below threshold.
    """
    # Design constraints:
    # - The largest x0-gap must be between the RIGHT body column and the
    #   sidebar, so cluster 1 = only the sidebar and H6 sees a thin/tall
    #   minority. Body columns are placed close together (x=60 and
    #   x=180) so their intra-body gap is smaller than body-to-sidebar.
    # - Two body columns must be interleaved in y-order so the
    #   POST-EXCLUSION geometry still fires the baseline detector on
    #   the remaining blocks.
    body_blocks: list[dict[str, Any]] = []
    idx = 0
    left_texts = [
        "Left column paragraph number one, has enough words to count.",
        "Left column continues with more sentences for word count.",
        "Left column third paragraph with sufficient content length.",
        "Left column fourth paragraph continuing the body text stream.",
        "Left column fifth paragraph, still going with body content.",
        "Left column sixth paragraph with enough body words.",
        "Left column seventh paragraph, penultimate on this side.",
        "Left column final paragraph on the two-column body layout.",
    ]
    right_texts = [
        "Right column paragraph one, matches the length of the left.",
        "Right column continues alongside the left column body flow.",
        "Right column third paragraph matches left in vertical position.",
        "Right column fourth paragraph continues the body text.",
        "Right column fifth paragraph, still tracking the left column.",
        "Right column sixth paragraph matches left in overall word count.",
        "Right column seventh paragraph, penultimate on this side too.",
        "Right column final paragraph closes out the two body columns.",
    ]
    y = 100.0
    for lt, rt in zip(left_texts, right_texts):
        body_blocks.append(_blk(idx, "paragraph", x0=60.0, y0=y, content=lt))
        idx += 1
        # Right column block placed at a slightly offset y so it lands
        # AFTER the left block in y-sorted order.
        body_blocks.append(_blk(idx, "paragraph", x0=180.0, y0=y + 4.0, content=rt))
        idx += 1
        y += 78.0
    # Sidebar at x=500 — 4 short markers with 1-char content.
    sidebar_blocks: list[dict[str, Any]] = []
    for y0 in (120.0, 260.0, 420.0, 620.0):
        sidebar_blocks.append(_blk(idx, "heading", x0=500.0, y0=y0, content="⁋"))
        idx += 1
    return {
        "name": "mixed_multicolumn_and_sidebar_page",
        "page_width": _PAGE_W,
        "page_height": _PAGE_H,
        "blocks": _sort_by_y(body_blocks + sidebar_blocks),
        "expected_baseline_warn": True,
        "expected_cluster_exclusion_warn": True,
        "expected_multicolumn": True,
        "expected_h6_matches": True,
        "expected_blanket_suppression_warn": False,
        "notes": ("Genuine two-column body PLUS right-margin sidebar. "
                  "Largest x-gap sits between right body column (x=180) and "
                  "sidebar (x=500), so H6 sees sidebar as the minority cluster. "
                  "Baseline warns correctly. Blanket suppression would "
                  "INCORRECTLY silence. Cluster exclusion removes only the "
                  "sidebar and recomputes: the two body columns' 60-vs-180 gap "
                  "becomes the new largest gap and the recomputed baseline "
                  "still fires."),
    }


def single_column_control() -> dict[str, Any]:
    """Clean single-column page. Baseline stays silent."""
    blocks = [
        _blk(i, "paragraph", x0=72.0, y0=100.0 + 90 * i,
             content=("Single-column body paragraph. " * 20).strip())
        for i in range(7)
    ]
    return {
        "name": "single_column_control",
        "page_width": _PAGE_W,
        "page_height": _PAGE_H,
        "blocks": blocks,
        "expected_baseline_warn": False,
        "expected_cluster_exclusion_warn": False,
        "expected_multicolumn": False,
        "expected_h6_matches": False,
        "notes": "single-column control; baseline stays silent",
    }


FIXTURES = [
    sidebar_only_page,
    true_three_column_page,
    mixed_multicolumn_and_sidebar_page,
    single_column_control,
]
