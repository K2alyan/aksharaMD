# A2 first-pass hallucination review — 2026-07-20

Manual review of the three assets flagged by the automated
`output_chars > 3 × hidden_text_layer_chars` rule during the A2
first pass.

Reviewer's fixed classification enum (per plan directive):

- `true_hallucination`
- `repetition_or_looping`
- `legitimate_visual_content`
- `minor_inflation`
- `automated_rule_false_positive`
- `unreviewable`

Method: re-invoked `_UnlimitedOcrRunner.infer_pdf` on each flagged
asset with the same production commit / model revision / harness
config and captured the raw model output. No post-processing before
review. Two of three completed cleanly; the third
(cropped/rotated/scaled) was killed after ~2 hours of stuck
generation because the classification was already decisive from
the streamed output.

---

## 1. `public/008-reportlab-inline-image/inline-image.pdf`

**Classification: `automated_rule_false_positive`**

- Hidden text-layer chars: 5
- Output chars: 37 (`<PAGE>\n![](images/page_0_0.jpg)\n\nTest`)
- Ratio triggered: 7.4×
- Absolute excess: 32 chars

The output is `<PAGE>` + a Markdown image placeholder + the actual
extracted text `Test`. The 3× rule fired only because the hidden
text-layer is tiny (5 chars); the absolute excess is a single
image-tag string that is *structurally correct* output for a PDF
that has an embedded image. Reviewer notes: not hallucination, not
inflation, just a small denominator. Same behavior in Marker /
other adapters would be similarly flagged by this rule.

**Recommendation for the rule:** add an absolute-excess floor to
the plan A4 hallucination-detection formula (e.g., skip the ratio
check when `output_chars < 200` or when the entire excess is
covered by a single well-formed `![](...)` tag).

---

## 2. `public/028-image-references-deduplication/wrong-references.pdf`

**Classification: `legitimate_visual_content` (with minor hallucination flag noted)**

- Hidden text-layer chars: 30
- Output chars: 582
- Ratio triggered: 19.4×

The bulk of the output is a legitimate file-listing extraction from
the document (`.gitignore`, `.pre-commit-config.y`, `.readthedocs.yaml`,
`CHANGELOG.md`, `CONTRIBUTING.md`, `CONTRIBUTORS.md`, `LICENSE`,
`make_release.py`, `Makefile`, `pyproject.toml`, `README.md`,
`.gitmodules`) — this is real visual content in the PDF that isn't
in the hidden text layer. **However**, the output also contains
three garbled non-English tokens with no plausible source in the
PDF:

- `Sv rv edferv vr`  (header of the first page)
- `Cacasc`  (top of page 2)
- `Coacac`  (top of page 3)

These look like decoder token-noise rather than legitimate
extraction — small localized hallucinations. Reviewer's read: the
file-listing bulk (~500 chars) is real; the ~20-40 chars of
garbled tokens are minor hallucination that would not corrupt any
downstream RAG / search use case in isolation, but should be
recorded.

**Recommendation:** classify overall as `legitimate_visual_content`,
note the isolated garbled-header hallucinations as a "watch" for
future runs. Does NOT fail the plan A4 "zero severe hallucination"
gate — severe would be *ballooning* or *invented headings that
change semantic meaning of the document*.

---

## 3. `public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf`

**Classification: `repetition_or_looping`**

- Hidden text-layer chars: 416
- Output chars (first pass): 36,940 (ratio 88.8×)
- Output chars (inspection re-run): ≥74,000 at the point of
  process kill (2+ hours in, no natural completion)
- Ratio triggered: 88.8× first pass; larger on re-run

**Decisive evidence of a stuck decoder loop.** Of 995 non-blank
model-emitted lines in the inspection stream, **950 are identical
in structure**:

```
<|det|>header [X, Y, X', Y']<|/det|>\( \therefore m = \frac{3}{11} \)
```

Only the bounding-box coordinates change; the payload is
byte-identical every time (`\( \therefore m = \frac{3}{11} \)`).
The model iterates through the pixel grid emitting one repeated
"header" detection per bbox until it hits `max_length` or
generates itself into a corner.

**Baidu's `no_repeat_ngram_size=35` guard failed on this input.**
The reason is subtle: the 35-token no-repeat window includes the
bbox coordinates, which change every emission — so from an n-gram
perspective every line IS different, even though the semantic
content (the LaTeX payload) is the same.

**Additional finding: runtime nondeterminism.** The same asset
under the same runner / same commit ran in 1,048 s on the first
pass but was still generating after 2+ hours on the inspection
re-run, producing at least 2× more output. Unlimited-OCR's
autoregressive decoder is stochastic, and the effect on total
runtime is very large in the pathological-loop case.

**Severity for the plan A4 gate:** this IS a severe hallucination
event by any reasonable reading — the model emitted a mathematical
formula that appears **nowhere in the source document** (or at
least not with the frequency observed) and looped on it. The
current rule fires correctly here. Recording this as **1 severe
hallucination** against threshold 2 (target: 0).

**Additional operational finding:** the inspection re-run exceeded
the 1,200 s per-asset soft timeout by a wide margin (still
running at 7,200+ s). If the deterministic second pass hits the
same loop, the 1,200 s timeout will fire and the second-pass row
will record `TIMEOUT` for this asset. The first-pass 1,048 s was
just under the ceiling by chance.

---

## Determinism impact on the deterministic second pass

Given asset #3's confirmed nondeterminism (1,048 s vs 7,200+ s
runtime for the same input), **the deterministic second pass will
NOT produce byte-identical output for this asset**, and probably
not for `parsebench/japanese_case` either (A1.5 saw 508 s vs 584 s
for that asset across two runs — 13 % variance).

Determinism comparison for the second pass should therefore use:

- **Hash equality** — expected for most successful assets;
  cropped/rotated will differ.
- **Character-count equality** — expected identical for 40+ of 42
  successful assets.
- **Failure-category equality** — expected identical for all 3
  failures (both GeoTopo → OOM, unreadablemetadata → other_exception).
- **Runtime equality** — explicitly NOT a determinism criterion.

---

## Summary

| Asset | Classification | Repetition ratio | Duplicate lines | Notes |
|---|---|---:|---:|---|
| `inline-image.pdf` | `automated_rule_false_positive` | n/a (37 chars) | 0 | Small denominator; single image tag = legitimate structure |
| `wrong-references.pdf` | `legitimate_visual_content` (minor hallucination noted) | ~0 | 0 | File listing is real; 3 garbled header strings are minor decoder noise |
| `cropped-rotated-scaled.pdf` | **`repetition_or_looping`** | 950 / 995 (95.5 %) | Same line 950× | Severe. Baidu's no_repeat_ngram guard defeated by varying bbox coords |

**Severe hallucination count against Phase A threshold 2: 1** (target: 0).

This is a genuine finding that must be recorded in the ADR, not
patched away. It reveals a real limitation of Unlimited-OCR's
loop-prevention on documents with dense repeating detection regions.
