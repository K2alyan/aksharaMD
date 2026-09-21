"""Stage 2 olmOCR scorer — core logic.

For each (canonical_id, parser_id) execution record produced by Stage 1,
this module:

1. Re-runs the same parser adapter (replay).
2. Verifies the output SHA-256 against the Stage 1 record.
3. Runs the olmOCR benchmark unit tests for that document.
4. Runs AksharaMD readiness scoring on the replayed markdown.
5. Writes a ``stage2_olmocr_result.json`` next to the execution record.

Public API
----------
load_all_unit_tests(bench_data_dir) -> dict[str, list]
replay_and_score(
    record_path, pdf_dir, unit_tests, root, benchmark_test_inventory_sha256,
    expected_assertions_by_document
) -> dict
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.stage2.olmocr_hygiene import (
    STAGE2_SCORER_CONTRACT_ID,
    AssertionInventory,
    assertion_set_sha256,
)

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# olmOCR upstream location (added to sys.path on first import of unit-test loader).
_OLMOCR_UPSTREAM_SUBDIR = Path("tmp") / "olmocr-upstream"

# JSONL category file → canonical_id prefix mapping.
# (table_tests.jsonl uses "tables/" prefix on disk.)
_CATEGORY_PREFIX: dict[str, str] = {
    "arxiv_math": "arxiv_math/",
    "headers_footers": "headers_footers/",
    "long_tiny_text": "long_tiny_text/",
    "multi_column": "multi_column/",
    "old_scans": "old_scans/",
    "old_scans_math": "old_scans_math/",
    "table_tests": "tables/",
}

# Readiness band thresholds (inclusive lower bound).
_BAND_HIGH = 85
_BAND_OK = 70
_BAND_RISKY = 50

# Adapter construction mirrors Stage 1 runner.build_adapters().
_PENDING_SHA = "0" * 64

_PARSER_CONFIGS = {
    "aksharamd-reference": dict(
        package_version="0.3.6",
        timeout_seconds=120.0,
        is_vlm=False,
        parser_model_version=None,
        parser_model_artifact_sha256=None,
    ),
    "marker": dict(
        package_version="1.10.2",
        timeout_seconds=600.0,
        is_vlm=True,
        parser_model_version="runtime",
        parser_model_artifact_sha256=None,
    ),
    "docling": dict(
        package_version="2.107.0",
        timeout_seconds=600.0,
        is_vlm=True,
        parser_model_version="runtime",
        parser_model_artifact_sha256=None,
    ),
    "markitdown": dict(
        package_version="0.1.6",
        timeout_seconds=120.0,
        is_vlm=False,
        parser_model_version=None,
        parser_model_artifact_sha256=None,
    ),
}

_WORKER_ARGV_PREFIX = [
    "python",
    "-m",
    "benchmarks.eval_v1.smoke_b1a_7b.workers.main",
]

_OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "DOCLING_ARTIFACTS_OFFLINE": "1",
}

STAGE2_SCHEMA_VERSION = "3"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _readiness_band(score: float) -> str:
    if score >= _BAND_HIGH:
        return "HIGH"
    if score >= _BAND_OK:
        return "OK"
    if score >= _BAND_RISKY:
        return "RISKY"
    return "POOR"


# ---------------------------------------------------------------------------
# Unit-test loader.
# ---------------------------------------------------------------------------


def _load_assertion_rows(
    bench_data_dir: Path,
    *,
    expected_document_count: int = 1403,
) -> dict[str, list[tuple[dict[str, Any], Path, int]]]:
    """Parse every frozen assertion row without any skip-on-error path."""
    rows: dict[str, list[tuple[dict[str, Any], Path, int]]] = {}
    assertion_ids: set[str] = set()
    for jsonl_name, canonical_prefix in _CATEGORY_PREFIX.items():
        jsonl_path = bench_data_dir / f"{jsonl_name}.jsonl"
        if not jsonl_path.is_file():
            raise RuntimeError(f"missing benchmark assertion file: {jsonl_path}")
        with jsonl_path.open(encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"malformed benchmark assertion at {jsonl_path}:{line_number}: {exc}"
                    ) from exc
                if not isinstance(data, dict):
                    raise RuntimeError(f"benchmark assertion is not an object at {jsonl_path}:{line_number}")
                for field in ("id", "pdf", "type"):
                    if not isinstance(data.get(field), str) or not data[field]:
                        raise RuntimeError(
                            f"benchmark assertion missing {field!r} at {jsonl_path}:{line_number}"
                        )
                assertion_id = data["id"]
                if assertion_id in assertion_ids:
                    raise RuntimeError(
                        f"duplicate benchmark assertion id {assertion_id!r} at {jsonl_path}:{line_number}"
                    )
                assertion_ids.add(assertion_id)
                pdf_field = data["pdf"]
                if not pdf_field.endswith(".pdf"):
                    raise RuntimeError(
                        f"benchmark assertion PDF lacks .pdf suffix at {jsonl_path}:{line_number}"
                    )
                canonical_id = pdf_field.removesuffix(".pdf")
                if not canonical_id.startswith(canonical_prefix):
                    raise RuntimeError(
                        f"benchmark assertion category mismatch at "
                        f"{jsonl_path}:{line_number}: {canonical_id!r}"
                    )
                rows.setdefault(canonical_id, []).append((data, jsonl_path, line_number))
    if len(rows) != expected_document_count:
        raise RuntimeError(
            f"benchmark assertion inventory covers {len(rows)} documents; expected {expected_document_count}"
        )
    return rows


def load_all_unit_tests(
    bench_data_dir: Path,
    *,
    expected_document_count: int = 1403,
    expected_assertions_by_document: AssertionInventory | None = None,
) -> dict[str, list]:
    """Load all olmOCR benchmark unit tests.

    Returns a mapping from canonical_id (e.g. ``arxiv_math/2502.15977_pg21``)
    to a list of test objects (each has a ``.run(md: str) -> (bool, str)``
    method).

    ``bench_data_dir`` must be the directory that contains the ``*.jsonl``
    files (``ROOT/tmp/olmocr-full-data/bench_data``).
    """
    # Ensure the olmOCR upstream package is importable.
    root = bench_data_dir.parent.parent  # tmp/olmocr-full-data -> tmp -> root
    # Re-derive root properly: bench_data_dir = ROOT/tmp/olmocr-full-data/bench_data
    # so root = bench_data_dir.parent.parent.parent would overshoot; we rely on the
    # caller passing ROOT-relative paths, but we'll resolve from bench_data_dir.
    # The safe approach: walk upward until we find the marker file.
    candidate = bench_data_dir
    for _ in range(6):
        if (candidate / "pyproject.toml").exists():
            root = candidate
            break
        candidate = candidate.parent

    olmocr_upstream = root / _OLMOCR_UPSTREAM_SUBDIR
    if str(olmocr_upstream) not in sys.path:
        sys.path.insert(0, str(olmocr_upstream))

    try:
        from olmocr.bench.tests import load_single_test  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "olmOCR bench test classes not importable. "
            f"Ensure {olmocr_upstream} exists and contains the olmocr package.\n"
            f"Detail: {exc}"
        ) from exc

    expected_rows = _load_assertion_rows(bench_data_dir, expected_document_count=expected_document_count)
    parsed_signatures = {
        canonical_id: tuple((data["id"], data["type"]) for data, _, _ in rows)
        for canonical_id, rows in expected_rows.items()
    }
    if expected_assertions_by_document is not None and parsed_signatures != expected_assertions_by_document:
        raise RuntimeError("parsed assertion IDs/types differ from verified frozen inventory")
    unit_tests: dict[str, list] = {}

    for canonical_id, rows in expected_rows.items():
        for data, jsonl_path, line_number in rows:
            # pdf field like "arxiv_math/2502.15977_pg21.pdf"
            # canonical_id = pdf field without ".pdf"
            # Ensure the category prefix is correct (table_tests → tables/).
            # The canonical_id derived from the pdf field already has the
            # right prefix (the JSONL stores the on-disk path).
            try:
                test_obj = load_single_test(data)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"unsupported benchmark assertion at {jsonl_path}:{line_number} id={data['id']!r}: {exc}"
                ) from exc
            loaded_id = getattr(test_obj, "id", None)
            loaded_type = getattr(test_obj, "type", None)
            if (loaded_id, loaded_type) != (data["id"], data["type"]):
                raise RuntimeError(
                    f"loaded assertion identity/type drift at "
                    f"{jsonl_path}:{line_number}: expected={(data['id'], data['type'])!r} "
                    f"actual={(loaded_id, loaded_type)!r}"
                )
            unit_tests.setdefault(canonical_id, []).append(test_obj)

    expected_signatures = {
        canonical_id: list(signatures) for canonical_id, signatures in parsed_signatures.items()
    }
    loaded_signatures = {
        canonical_id: [(getattr(test, "id", None), getattr(test, "type", None)) for test in tests]
        for canonical_id, tests in unit_tests.items()
    }
    if loaded_signatures != expected_signatures:
        raise RuntimeError("loaded benchmark assertion IDs/counts differ from frozen inventory")
    return unit_tests


# ---------------------------------------------------------------------------
# Adapter factory.
# ---------------------------------------------------------------------------


def _build_adapter(parser_id: str):
    """Build a SubprocessParserAdapter for the given parser_id."""
    from benchmarks.eval_v1.smoke_b1a_7b.real_adapters import (  # noqa: PLC0415
        RealAdapterConfig,
        SubprocessParserAdapter,
    )
    from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import (  # noqa: PLC0415
        RealSubprocessInvoker,
    )

    cfg_kwargs = _PARSER_CONFIGS[parser_id]
    config = RealAdapterConfig(
        parser_id=parser_id,
        package_source_sha256=_PENDING_SHA,
        adapter_source_sha256=_PENDING_SHA,
        **cfg_kwargs,
    )
    return SubprocessParserAdapter(
        config=config,
        subprocess_invoker=RealSubprocessInvoker(),
        worker_argv_prefix=_WORKER_ARGV_PREFIX,
        offline_env=_OFFLINE_ENV,
    )


# ---------------------------------------------------------------------------
# Aksharamd scoring helper.
# ---------------------------------------------------------------------------


def _run_aksharamd_scoring(markdown: str) -> tuple[float | None, list[str], str | None]:
    """Write markdown to a temp file, compile, score.

    SCORING CONTRACT (V1 declared limitation):
      Scores the parser's Markdown output as a Markdown document, NOT the
      original source PDF. Format baseline is 95 (Markdown), not 87 (PDF,
      frozen in manifest §1). Source/geometry detectors (W_TABLE_MISSING,
      W_DROPPED_CONTENT) that require a source PDF are NOT activated and are
      UNMEASURED in V1. This is an explicit declaration of scope.

    Returns (readiness_score, warning_codes, error_message).
    error_message is None on success.
    """
    try:
        from aksharamd.compiler import Compiler  # noqa: PLC0415
        from aksharamd.scoring.readiness import compute_readiness_score  # noqa: PLC0415

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp.write(markdown)
            tmp_path = Path(tmp.name)

        try:
            ctx = Compiler().compile(str(tmp_path))
            score: float = float(compute_readiness_score(ctx))
            codes: list[str] = [w.code for w in ctx.validation.warnings]
            return score, codes, None
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        return None, [], traceback.format_exc(limit=5)


# ---------------------------------------------------------------------------
# Main scoring entry point.
# ---------------------------------------------------------------------------


def replay_and_score(
    record_path: Path,
    pdf_dir: Path,
    unit_tests: dict[str, list],
    root: Path,
    benchmark_test_inventory_sha256: str,
    expected_assertions_by_document: AssertionInventory,
) -> dict[str, Any]:
    """Replay a Stage 1 execution record and produce a Stage 2 result dict.

    Parameters
    ----------
    record_path:
        Path to ``execution_record.json``.
    pdf_dir:
        Directory containing PDFs at ``{canonical_id}.pdf`` sub-paths.
    unit_tests:
        Mapping returned by ``load_all_unit_tests()``.
    root:
        Repository root (used for sys.path setup if needed).

    Returns
    -------
    dict
        Matches the stage2_olmocr_result.json schema.
    """
    # ------------------------------------------------------------------ #
    # 1. Load Stage 1 record.
    # ------------------------------------------------------------------ #
    record: dict[str, Any] = json.loads(record_path.read_text(encoding="utf-8"))
    canonical_id: str = record["canonical_id"]
    parser_id: str = record["parser_id"]
    exit_status: str = record.get("exit_status", "")
    expected_assertions = expected_assertions_by_document.get(canonical_id, ())

    source_pdf_sha256: str | None = None

    def _base(status: str) -> dict[str, Any]:
        return {
            "stage2_schema_version": STAGE2_SCHEMA_VERSION,
            "canonical_id": canonical_id,
            "parser_id": parser_id,
            "status": status,
            "sha_verified": False,
            "replay_defect_reason": None,
            "readiness_score": None,
            "readiness_band": None,
            "warning_codes": [],
            "n_tests": 0,
            "n_passed": 0,
            "n_failed": 0,
            "test_results": [],
            "scored_at": _now_utc(),
            "stage2_scorer_contract_id": STAGE2_SCORER_CONTRACT_ID,
            "benchmark_test_inventory_sha256": benchmark_test_inventory_sha256,
            "expected_assertion_count": len(expected_assertions),
            "assertion_set_sha256": assertion_set_sha256(expected_assertions),
            "stage1_execution_manifest_sha256": record.get("stage1_execution_manifest_sha256"),
            "stage1_output_sha256": record.get("output_sha256"),
            "stage1_exit_status": exit_status,
            "source_pdf_sha256": source_pdf_sha256,
        }

    # ------------------------------------------------------------------ #
    # 2. DEFECT in Stage 1 → skip replay.
    # ------------------------------------------------------------------ #
    if exit_status == "DEFECT":
        result = _base("SKIPPED_DEFECT")
        result["replay_defect_reason"] = record.get("defect_reason")
        return result

    expected_sha: str | None = record.get("output_sha256")

    # ------------------------------------------------------------------ #
    # 3. Load PDF bytes.
    # ------------------------------------------------------------------ #
    pdf_path = pdf_dir / f"{canonical_id}.pdf"
    if not pdf_path.exists():
        result = _base("REPLAY_DEFECT")
        result["replay_defect_reason"] = f"pdf_not_found:{pdf_path}"
        return result

    pdf_bytes = pdf_path.read_bytes()
    source_pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    # ------------------------------------------------------------------ #
    # 4. Build adapter.
    # ------------------------------------------------------------------ #
    if parser_id not in _PARSER_CONFIGS:
        result = _base("REPLAY_DEFECT")
        result["replay_defect_reason"] = f"unknown_parser_id:{parser_id}"
        return result

    adapter = _build_adapter(parser_id)

    # ------------------------------------------------------------------ #
    # 5. Re-run parser.
    # ------------------------------------------------------------------ #
    outcome = adapter.compile(pdf_bytes, canonical_id)

    from benchmarks.eval_v1.smoke_b1a_7b.adapter_protocol import ParseStatus  # noqa: PLC0415

    if outcome.status == ParseStatus.DEFECT:
        result = _base("REPLAY_DEFECT")
        result["replay_defect_reason"] = outcome.defect_reason
        return result

    markdown: str = outcome.markdown  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # 6. Verify SHA.
    # ------------------------------------------------------------------ #
    actual_sha = _sha256(markdown)
    sha_verified = (expected_sha is not None) and (actual_sha == expected_sha)

    if not sha_verified:
        result = _base("SHA_MISMATCH")
        result["sha_verified"] = False
        result["replay_defect_reason"] = f"expected={expected_sha} actual={actual_sha}"
        return result

    # ------------------------------------------------------------------ #
    # 7. Run unit tests.
    # ------------------------------------------------------------------ #
    doc_tests = unit_tests.get(canonical_id, [])
    test_results: list[dict[str, Any]] = []
    n_passed = 0
    n_failed = 0

    for test_obj in doc_tests:
        test_id: str = getattr(test_obj, "id", "")
        test_type: str = getattr(test_obj, "type", "")
        try:
            outcome_run = test_obj.run(markdown)
            # run() returns (bool, explanation_str)
            if isinstance(outcome_run, tuple):
                passed, explanation = outcome_run
            else:
                passed = bool(outcome_run)
                explanation = ""
            error_msg = None if passed else str(explanation)
        except Exception:  # noqa: BLE001
            passed = False
            error_msg = traceback.format_exc(limit=3)

        if passed:
            n_passed += 1
        else:
            n_failed += 1

        test_results.append(
            {
                "test_id": test_id,
                "test_type": test_type,
                "passed": passed,
                "error": error_msg,
            }
        )

    # ------------------------------------------------------------------ #
    # 8. AksharaMD readiness scoring.
    # ------------------------------------------------------------------ #
    readiness_score, warning_codes, scoring_error = _run_aksharamd_scoring(markdown)

    # ------------------------------------------------------------------ #
    # 9. Assemble result.
    # ------------------------------------------------------------------ #
    status = "SCORED"
    if not test_results:
        status = "NO_BENCHMARK_TESTS"
    elif scoring_error is not None:
        status = "SCORING_ERROR"
    result = _base(status)
    result["sha_verified"] = True
    result["readiness_score"] = readiness_score
    result["readiness_band"] = _readiness_band(readiness_score) if readiness_score is not None else None
    result["warning_codes"] = warning_codes
    result["n_tests"] = len(test_results)
    result["n_passed"] = n_passed
    result["n_failed"] = n_failed
    result["test_results"] = test_results
    if scoring_error is not None:
        result["scoring_error"] = scoring_error
    return result
