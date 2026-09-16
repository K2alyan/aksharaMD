"""Worker dispatch tests — monkeypatched adapters, no real parsers.

Each worker branch imports its adapter class lazily via
``from benchmarks.parsed_vs_raw.adapters.<name>_adapter import <Class>``.
We monkeypatch ``sys.modules`` with a fake adapter module so the
worker's dispatch shape is verified without importing any real
parser package. Conftest guards on ``marker`` / ``docling`` /
``markitdown`` top-level imports would fire otherwise.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import types
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------
# Fake ParsedArtifact / ParserInput injection.
#
# The workers call ``adapter.parse(ParserInput(...))`` and expect a
# ``ParsedArtifact`` back with ``.content`` bytes. The real
# aksharamd.parser_contract module validates SHAs on construction, so
# our fakes must satisfy that shape.


def _make_fake_adapter_module(
    class_name: str,
    *,
    markdown: str = "# fake\n",
    raise_exc: BaseException | None = None,
    call_recorder: list | None = None,
) -> types.ModuleType:
    """Build a synthetic module exposing a class of the given name.
    The class's ``parse()`` either returns a ParsedArtifact-like
    object or raises the given exception.
    """
    from aksharamd.parser_contract import ParsedArtifact

    calls: list = call_recorder if call_recorder is not None else []

    @dataclass
    class _FakeAdapter:
        _parser_version: str = "fake"

        def __post_init__(self) -> None:
            calls.append(("__post_init__", ()))

        def parse(self, source: Any) -> Any:
            calls.append(("parse", (source.source_id, source.source_hash,
                                    source.media_type, bytes(source.data))))
            if raise_exc is not None:
                raise raise_exc
            content = markdown.encode("utf-8")
            return ParsedArtifact(
                source_id=source.source_id,
                source_hash=source.source_hash,
                content=content,
                content_hash=hashlib.sha256(content).hexdigest(),
                content_mime_type="text/markdown",
                parser_name="fake",
                parser_version="fake",
                parser_configuration_id="default",
                declared_truncated=False,
            )

    mod = types.ModuleType(f"fake_adapter_module_{class_name}")
    setattr(mod, class_name, _FakeAdapter)
    return mod


def _capture_stdio(monkeypatch) -> tuple[io.StringIO, io.BytesIO, io.StringIO]:
    """Redirect stdin/stdout/stderr so the worker can be driven from
    a Python test. Returns (stdin, stdout, stderr) captures. The
    stdout capture is a StringIO (workers write text). ``sys.stdin``
    is replaced by a small object exposing a ``.buffer`` attribute
    (workers read PDF bytes from ``sys.stdin.buffer``)."""
    stdin_bytes = io.BytesIO()

    class _StdinShim:
        buffer = stdin_bytes

    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _StdinShim())
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    return stdin_bytes, stdout, stderr


def _decode_worker_output(stdout: io.StringIO) -> dict:
    text = stdout.getvalue().strip()
    return json.loads(text)


# --------------------------------------------------------------------------
# Marker.


def test_marker_worker_dispatches_to_MarkerAdapter_parse(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.marker_adapter",
        _make_fake_adapter_module("MarkerAdapter",
                                   markdown="# marker md\n",
                                   call_recorder=calls),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\nfake-marker-payload")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main([
        "--parser-id", "marker",
        "--canonical-id", "SYN-PMC-1",
    ])
    assert exit_code == 0
    payload = _decode_worker_output(stdout)
    assert payload["status"] == "EXECUTED"
    assert payload["markdown"] == "# marker md\n"

    # Adapter was constructed exactly once and parse() was called once.
    kinds = [c[0] for c in calls]
    assert kinds == ["__post_init__", "parse"]
    _, (src_id, src_hash, media, data) = calls[1]
    assert src_id == "SYN-PMC-1"
    assert media == "application/pdf"
    assert data == b"%PDF-1.5\nfake-marker-payload"
    assert src_hash == hashlib.sha256(data).hexdigest()


def test_marker_worker_maps_cuda_oom_to_coded_reason(monkeypatch) -> None:
    err = RuntimeError("CUDA out of memory. Tried to allocate 512.00 MiB")
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.marker_adapter",
        _make_fake_adapter_module("MarkerAdapter", raise_exc=err),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main(["--parser-id", "marker", "--canonical-id", "X"])
    assert exit_code == 3
    payload = _decode_worker_output(stdout)
    assert payload == {"status": "DEFECT", "defect_reason": "marker_cuda_oom"}


def test_marker_worker_maps_cuda_unavailable_to_coded_reason(monkeypatch) -> None:
    err = RuntimeError("Torch not compiled with CUDA enabled")
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.marker_adapter",
        _make_fake_adapter_module("MarkerAdapter", raise_exc=err),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main(["--parser-id", "marker", "--canonical-id", "X"])
    assert exit_code == 3
    payload = _decode_worker_output(stdout)
    assert payload["defect_reason"] == "marker_cuda_unavailable"


def test_marker_worker_generic_exception_falls_back(monkeypatch) -> None:
    err = ValueError("something unrelated blew up")
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.marker_adapter",
        _make_fake_adapter_module("MarkerAdapter", raise_exc=err),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    main(["--parser-id", "marker", "--canonical-id", "X"])
    payload = _decode_worker_output(stdout)
    assert payload["defect_reason"] == "marker_exception:ValueError"


# --------------------------------------------------------------------------
# Docling.


def test_docling_worker_dispatches_to_DoclingAdapter_parse(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.docling_adapter",
        _make_fake_adapter_module("DoclingAdapter",
                                   markdown="",  # docling empty-on-success
                                   call_recorder=calls),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\nfake-docling")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main(["--parser-id", "docling", "--canonical-id", "SYN-DL-1"])
    assert exit_code == 0
    payload = _decode_worker_output(stdout)
    assert payload["status"] == "EXECUTED"
    assert payload["markdown"] == ""  # empty stays EXECUTED
    assert [c[0] for c in calls] == ["__post_init__", "parse"]


def test_docling_worker_cuda_oom_mapping(monkeypatch) -> None:
    err = RuntimeError("CUDA out of memory something")
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.docling_adapter",
        _make_fake_adapter_module("DoclingAdapter", raise_exc=err),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    main(["--parser-id", "docling", "--canonical-id", "X"])
    payload = _decode_worker_output(stdout)
    assert payload["defect_reason"] == "docling_cuda_oom"


# --------------------------------------------------------------------------
# MarkItDown.


def test_markitdown_worker_dispatches_to_MarkItDownAdapter_parse(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.markitdown_adapter",
        _make_fake_adapter_module("MarkItDownAdapter",
                                   markdown="# markitdown\n",
                                   call_recorder=calls),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\nfake-md")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main(["--parser-id", "markitdown", "--canonical-id", "SYN-FR-1"])
    assert exit_code == 0
    payload = _decode_worker_output(stdout)
    assert payload["markdown"] == "# markitdown\n"
    assert [c[0] for c in calls] == ["__post_init__", "parse"]


def test_markitdown_worker_generic_exception(monkeypatch) -> None:
    err = RuntimeError("markitdown broke")
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.markitdown_adapter",
        _make_fake_adapter_module("MarkItDownAdapter", raise_exc=err),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    main(["--parser-id", "markitdown", "--canonical-id", "X"])
    payload = _decode_worker_output(stdout)
    assert payload["defect_reason"] == "markitdown_exception:RuntimeError"


# --------------------------------------------------------------------------
# aksharamd-reference goes through smoke_run_v2._compile_pdf_bytes.


def test_reference_worker_dispatches_to_compile_pdf_bytes(monkeypatch) -> None:
    """The reference worker uses ``_compile_pdf_bytes`` (already
    imported by the smoke_run_v2 module) rather than an adapter. We
    monkeypatch the function to record the call and return a fake
    markdown string."""
    import benchmarks.parsed_vs_raw.arms.parser_arm as smoke_v2

    recorded: dict = {}

    def _fake(pdf_bytes: bytes) -> str:
        recorded["called_with"] = bytes(pdf_bytes)
        return "# reference synthetic md\n"

    monkeypatch.setattr(smoke_v2, "_compile_pdf_bytes", _fake)
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\nfake-ref")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main(["--parser-id", "aksharamd-reference",
                       "--canonical-id", "SYN-PMC-1"])
    assert exit_code == 0
    payload = _decode_worker_output(stdout)
    assert payload["markdown"] == "# reference synthetic md\n"
    assert recorded["called_with"] == b"%PDF-1.5\nfake-ref"


def test_reference_worker_generic_exception_uses_reference_prefix(monkeypatch) -> None:
    import benchmarks.parsed_vs_raw.arms.parser_arm as smoke_v2

    def _fake(pdf_bytes: bytes) -> str:
        raise KeyError("something in reference broke")

    monkeypatch.setattr(smoke_v2, "_compile_pdf_bytes", _fake)
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    main(["--parser-id", "aksharamd-reference", "--canonical-id", "X"])
    payload = _decode_worker_output(stdout)
    assert payload["defect_reason"] == "reference_parser_exception:KeyError"


# --------------------------------------------------------------------------
# CUDA classification does not apply to CPU-only parsers.


def test_cuda_message_from_markitdown_is_generic_not_cuda(monkeypatch) -> None:
    """A markitdown adapter that somehow surfaces a 'CUDA out of memory'
    error is still a generic exception at the smoke layer — CUDA-
    classification only applies to VLM parsers."""
    err = RuntimeError("CUDA out of memory (contrived for markitdown)")
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.parsed_vs_raw.adapters.markitdown_adapter",
        _make_fake_adapter_module("MarkItDownAdapter", raise_exc=err),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    main(["--parser-id", "markitdown", "--canonical-id", "X"])
    payload = _decode_worker_output(stdout)
    # Not markitdown_cuda_oom — CUDA classification is VLM-only.
    assert payload["defect_reason"] == "markitdown_exception:RuntimeError"
