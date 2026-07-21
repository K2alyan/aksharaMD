"""Tests for the pure exit-code classifier in the OCR worker.

Full ``main()`` requires torch and the model, so those paths run in
integration only. The classifier is pure and cheap; test it here.
"""
from __future__ import annotations

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_worker import (  # type: ignore
    EXIT_CUDA_CONTEXT_UNHEALTHY,
    EXIT_CUDA_OOM,
    EXIT_NON_OOM_INFER_FAILURE,
    _classify_exception_message,
)


def test_cuda_context_unhealthy_maps_to_context_unhealthy_exit():
    assert _classify_exception_message(
        "chunked_infer_failed: cuda_context_unhealthy_after_oom"
    ) == EXIT_CUDA_CONTEXT_UNHEALTHY


def test_outofmemory_error_maps_to_oom_exit():
    assert _classify_exception_message(
        "infer_failed: OutOfMemoryError: CUDA out of memory. Tried to allocate ..."
    ) == EXIT_CUDA_OOM


def test_cuda_out_of_memory_string_maps_to_oom_exit():
    assert _classify_exception_message(
        "infer_failed: RuntimeError: CUDA out of memory"
    ) == EXIT_CUDA_OOM


def test_accelerator_error_maps_to_oom_exit():
    """torch 2.12+ wraps CUDA OOM as AcceleratorError."""
    assert _classify_exception_message(
        "infer_failed: AcceleratorError: CUDA error: out of memory"
    ) == EXIT_CUDA_OOM


def test_non_oom_infer_failure_maps_to_non_oom_exit():
    assert _classify_exception_message(
        "infer_failed: ValueError: something else entirely"
    ) == EXIT_NON_OOM_INFER_FAILURE


def test_empty_message_maps_to_non_oom_exit():
    assert _classify_exception_message("") == EXIT_NON_OOM_INFER_FAILURE


def test_none_message_maps_to_non_oom_exit():
    assert _classify_exception_message(None) == EXIT_NON_OOM_INFER_FAILURE  # type: ignore[arg-type]


def test_classifier_is_case_insensitive():
    assert _classify_exception_message(
        "INFER_FAILED: OUTOFMEMORYERROR"
    ) == EXIT_CUDA_OOM
