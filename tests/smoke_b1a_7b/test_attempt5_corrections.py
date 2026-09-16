"""Regression tests for the three production defects found in real-smoke
Attempt #4 and the operational diagnostic addition (B1a-7b.2e corrections).

Defect 1 — reference-parser tuple unpacking:
  _compile_pdf_bytes returns (markdown, ctx); the worker was assigning the
  whole tuple to `md` → json.dumps(tuple) → TypeError.

Defect 2 — post-positive-control ordering:
  Post-control ran BEFORE firewall cleanup; the harness process was still
  blocked by its own rule, so the probe failed → INCONCLUSIVE. Correct
  lifecycle: cleanup → post-control.

Defect 3 — write_text CRLF translation on Windows:
  raw_path.write_text(raw_md, encoding="utf-8") translates \\n → \\r\\n on
  Windows; the recorded sha256/output_bytes were computed from LF bytes →
  mismatch on every EXECUTED pair. Fix: write_bytes(raw_md.encode("utf-8")).

Addition 4 — operational diagnostic artifact:
  For DEFECT pairs, the worker now writes AKSHARAMD_SMOKE_* structured lines
  to stderr and the harness writes operational_diagnostic.json to the pair
  directory. The file is operator-facing only — not in analysis_record.json,
  not in the review allowlist.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

from benchmarks.eval_v1.smoke_b1a_7b.contracts import load_contracts
from benchmarks.eval_v1.smoke_b1a_7b.environment import EnvironmentSnapshot
from benchmarks.eval_v1.smoke_b1a_7b.firewall import (
    NetworkEgressObservation,
    ProbeResult,
)
from benchmarks.eval_v1.smoke_b1a_7b.smoke_runner import (
    PositiveControlProbe,
    SmokeOutcome,
    SmokeRunner,
)
from tests.smoke_b1a_7b.fakes import (
    BlockedProbeBackend,
    FakeFirewallBackend,
    FakeResourceSampler,
    OpenProbeBackend,
    defect_response,
    executed_response,
    fake_vram_sampler_factory,
    make_four_adapters,
    make_three_synthetic_docs,
    positive_control_passing,
    positive_control_sequence,
)


def _pinned_env() -> EnvironmentSnapshot:
    c = load_contracts()
    return EnvironmentSnapshot(
        python_version=c.pinned_python_version(),
        platform_string=c.platform_prefix() + "-SP0",
        git_commit="deadbeef",
        cpu_physical_cores=8,
        cuda_version="12.6",
        cuda_driver_version="12.6",
        cuda_device_name="fake",
    )


def _runner(
    run_dir: Path,
    adapters,
    *,
    firewall_backend=None,
    positive_control=None,
    probe_backend=None,
) -> SmokeRunner:
    return SmokeRunner(
        contracts=load_contracts(),
        environment=_pinned_env(),
        smoke_documents=make_three_synthetic_docs(),
        adapters=adapters,
        probe_backend=probe_backend or BlockedProbeBackend(),
        firewall_backend=firewall_backend or FakeFirewallBackend(),
        positive_control=positive_control or positive_control_passing(),
        run_dir=run_dir,
        program_path_for_firewall="/fake",
        resource_sampler_factory=FakeResourceSampler,
        vram_sampler_factory=fake_vram_sampler_factory,
    )


def _open_tcp() -> ProbeResult:
    return ProbeResult(
        name="tcp_connect", connected=True,
        exc_class_name=None, errno_int=None,
        detail="fake: connected",
    )


def _open_https() -> ProbeResult:
    return ProbeResult(
        name="https_get", connected=True,
        exc_class_name=None, errno_int=None,
        detail="fake: http response",
    )


def _open_obs() -> NetworkEgressObservation:
    return NetworkEgressObservation(
        probe_tcp_connect=_open_tcp(),
        probe_https_get=_open_https(),
        blocked=False,
    )


# ---------------------------------------------------------------------------
# Defect 1 — reference-parser tuple unpacking.
# ---------------------------------------------------------------------------


def _capture_stdio(monkeypatch):
    stdin_bytes = io.BytesIO()

    class _StdinShim:
        buffer = stdin_bytes

    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _StdinShim())
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    return stdin_bytes, stdout, stderr


def test_reference_worker_unpacks_tuple_from_compile_pdf_bytes(monkeypatch) -> None:
    """Regression: _compile_pdf_bytes returns (markdown, ctx); the worker
    must unpack the tuple and pass only the markdown string to _emit_success.
    Before the fix, md=tuple → json.dumps(tuple) → TypeError on stdout."""
    import benchmarks.parsed_vs_raw.arms.parser_arm as arm

    def _fake_compile(pdf_bytes: bytes):
        return ("# reference output\n\nsome text.", {"ctx_key": "ctx_value"})

    monkeypatch.setattr(arm, "_compile_pdf_bytes", _fake_compile)

    stdin_bytes, stdout, stderr = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\nfake-ref")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main(["--parser-id", "aksharamd-reference", "--canonical-id", "SYN-PMC-1"])
    assert exit_code == 0
    payload = json.loads(stdout.getvalue().strip())
    assert payload["status"] == "EXECUTED"
    assert payload["markdown"] == "# reference output\n\nsome text."


def test_reference_worker_tuple_result_is_not_serialized_as_list(monkeypatch) -> None:
    """Pre-fix: json.dumps((markdown, ctx)) would serialize as a JSON array.
    This test pins the corrected shape: stdout is a dict with a string markdown."""
    import benchmarks.parsed_vs_raw.arms.parser_arm as arm

    monkeypatch.setattr(
        arm, "_compile_pdf_bytes",
        lambda b: ("# heading\n", {"unused": True}),
    )
    stdin_bytes, stdout, _ = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    main(["--parser-id", "aksharamd-reference", "--canonical-id", "X"])
    payload = json.loads(stdout.getvalue().strip())
    assert isinstance(payload.get("markdown"), str), (
        "markdown field must be str, not list/tuple serialized as JSON array"
    )


# ---------------------------------------------------------------------------
# Defect 2 — post-positive-control ordering.
# ---------------------------------------------------------------------------


def _make_ordering_probe_and_backend(call_order: list[str]):
    """Build a tracked FakeFirewallBackend and a PositiveControlProbe whose
    calls are recorded in call_order as 'fw_remove' and 'post_control'.

    The PositiveControlProbe is called twice (pre and post smoke).  We label
    them 'pre_control' and 'post_control' in sequence so ordering assertions
    can target the post call specifically."""
    fw_backend = FakeFirewallBackend()
    fw_backend.rule_present = True

    _orig_remove = fw_backend.remove_rule
    def _tracked_remove(*, display_name: str) -> None:
        _orig_remove(display_name=display_name)
        call_order.append("fw_remove")
    fw_backend.remove_rule = _tracked_remove  # type: ignore[method-assign]

    _pc_call_count: list[int] = [0]
    def _tracked_pc() -> NetworkEgressObservation:
        _pc_call_count[0] += 1
        label = "pre_control" if _pc_call_count[0] == 1 else "post_control"
        call_order.append(label)
        return _open_obs()

    return fw_backend, PositiveControlProbe(run=_tracked_pc)


def test_post_control_runs_after_firewall_cleanup(run_dir: Path) -> None:
    """Cleanup must precede post-control. If post-control runs first and the
    harness process is still blocked by the rule it owns, the probe fails."""
    call_order: list[str] = []
    fw_backend, pc = _make_ordering_probe_and_backend(call_order)

    runner = _runner(
        run_dir, make_four_adapters(),
        firewall_backend=fw_backend,
        positive_control=pc,
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS
    assert "fw_remove" in call_order, "cleanup was never called"
    assert "post_control" in call_order, "post-control was never called"
    fw_idx = call_order.index("fw_remove")
    post_idx = call_order.index("post_control")
    assert fw_idx < post_idx, (
        f"fw_remove at index {fw_idx}, post_control at {post_idx} — wrong order: {call_order}"
    )


def test_cleanup_runs_and_precedes_post_control_after_harness_stop(run_dir: Path) -> None:
    """When a harness-level stop fires mid-run (network_egress_not_blocked),
    the finally block must still: (a) run cleanup, (b) run post-control,
    and (c) in that order."""
    call_order: list[str] = []
    fw_backend, pc = _make_ordering_probe_and_backend(call_order)

    # OpenProbeBackend → per-invocation check sees egress NOT blocked →
    # harness emits _HarnessLevelStop("network_egress_not_blocked") after pair 1.
    runner = _runner(
        run_dir, make_four_adapters(),
        probe_backend=OpenProbeBackend(),
        firewall_backend=fw_backend,
        positive_control=pc,
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.FAIL
    assert "fw_remove" in call_order, "cleanup did not run after harness stop"
    assert "post_control" in call_order, "post-control did not run after harness stop"
    fw_idx = call_order.index("fw_remove")
    post_idx = call_order.index("post_control")
    assert fw_idx < post_idx, (
        f"fw_remove at index {fw_idx}, post_control at {post_idx}: {call_order}"
    )


def test_post_control_runs_even_after_cleanup_failure(run_dir: Path) -> None:
    """If cleanup raises FirewallRuleError (rule lingers), the error is captured
    but post-control must still run. The outcome is FAIL (cleanup broken)."""
    call_order: list[str] = []
    fw_backend = FakeFirewallBackend()
    fw_backend.rule_present = True
    fw_backend.linger_after_remove = True  # FirewallRuleManager.cleanup() → FirewallRuleError

    def _post_control_probe() -> NetworkEgressObservation:
        call_order.append("post_control")
        return _open_obs()

    runner = _runner(
        run_dir, make_four_adapters(),
        firewall_backend=fw_backend,
        positive_control=PositiveControlProbe(run=_post_control_probe),
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.FAIL
    assert result.reason.startswith("firewall_rule_cleanup_broken")
    assert "post_control" in call_order, (
        "post-control must run even after cleanup failure"
    )


def test_post_control_failure_yields_inconclusive(run_dir: Path) -> None:
    """Post-smoke positive control blocked → INCONCLUSIVE (not PASS, not FAIL)."""
    runner = _runner(
        run_dir, make_four_adapters(),
        positive_control=positive_control_sequence([True, False]),
    )
    result = runner.run()
    assert result.outcome is SmokeOutcome.INCONCLUSIVE
    assert result.reason == "post_smoke_positive_control_failed"
    assert result.positive_control_pre is not None
    assert result.positive_control_post is not None


# ---------------------------------------------------------------------------
# Defect 3 — write_bytes: on-disk SHA256/bytes must match execution record.
# ---------------------------------------------------------------------------


def test_raw_output_sha256_matches_on_disk_bytes(run_dir: Path) -> None:
    """Regression for Attempt #4 CRLF mismatch.

    write_text(raw_md, encoding='utf-8') on Windows translates \\n → \\r\\n,
    making on-disk bytes differ from the LF bytes used to compute sha256.
    write_bytes must be used so the hash and byte-length are byte-exact."""
    adapters = make_four_adapters(
        reference_response=executed_response("# heading\n\nline 1\nline 2\n"),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS

    for record_path in run_dir.rglob("execution_record.json"):
        record = json.loads(record_path.read_bytes())
        if record["exit_status"] != "EXECUTED":
            continue
        raw_path = record_path.parent / "raw_output.md"
        assert raw_path.exists()
        on_disk = raw_path.read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == record["output_sha256"], (
            f"SHA mismatch for {record_path}: on-disk bytes differ from recorded hash"
        )
        assert len(on_disk) == record["output_bytes"], (
            f"byte-length mismatch for {record_path}"
        )


def test_normalized_output_written_as_bytes_no_crlf(run_dir: Path) -> None:
    """normalized_output.md must also be written with write_bytes so there is no
    platform-dependent CRLF translation. Content must be pure LF on any OS."""
    adapters = make_four_adapters(
        reference_response=executed_response("# heading\n\ntext\n"),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS

    executed = [
        p.parent / "normalized_output.md"
        for p in run_dir.rglob("execution_record.json")
        if json.loads(p.read_bytes())["exit_status"] == "EXECUTED"
    ]
    assert executed, "no EXECUTED pairs found — test setup error"
    for norm_path in executed:
        assert norm_path.exists()
        raw_bytes = norm_path.read_bytes()
        assert b"\r\n" not in raw_bytes, (
            f"{norm_path}: CRLF found — write_text was used instead of write_bytes"
        )


# ---------------------------------------------------------------------------
# Addition 4 — operational diagnostic artifact.
# ---------------------------------------------------------------------------


def test_operational_diagnostic_written_for_defect_pairs(run_dir: Path) -> None:
    """Every DEFECT pair gets an operational_diagnostic.json in its pair directory."""
    adapters = make_four_adapters(
        reference_response=defect_response("reference_parser_exception:TypeError"),
    )
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS  # other parsers fine

    ref_pair_dirs = [
        p.parent for p in run_dir.rglob("execution_record.json")
        if "aksharamd-reference" in str(p)
    ]
    assert len(ref_pair_dirs) == 3

    for d in ref_pair_dirs:
        diag_path = d / "operational_diagnostic.json"
        assert diag_path.exists(), f"operational_diagnostic.json missing in {d}"
        diag = json.loads(diag_path.read_bytes())
        assert diag["exit_status"] == "DEFECT"
        assert diag["defect_reason"] == "reference_parser_exception:TypeError"
        assert "pair_id" in diag
        assert isinstance(diag["stderr_byte_length"], int)
        assert diag["stderr_path"] == "stderr.txt"


def test_operational_diagnostic_absent_for_executed_pairs(run_dir: Path) -> None:
    """EXECUTED pairs must NOT have an operational_diagnostic.json."""
    adapters = make_four_adapters()
    runner = _runner(run_dir, adapters)
    result = runner.run()
    assert result.outcome is SmokeOutcome.PASS

    for record_path in run_dir.rglob("execution_record.json"):
        record = json.loads(record_path.read_bytes())
        if record["exit_status"] == "EXECUTED":
            diag_path = record_path.parent / "operational_diagnostic.json"
            assert not diag_path.exists(), (
                f"operational_diagnostic.json must not exist for EXECUTED pair: {record_path}"
            )


def test_operational_diagnostic_not_in_analysis_record(run_dir: Path) -> None:
    """operational_diagnostic.json must not appear as a key inside
    analysis_record.json or reviewer_artifact. The review surface must remain
    clean of operator-only diagnostic content."""
    adapters = make_four_adapters(
        marker_response=defect_response("marker_timeout_600s"),
    )
    runner = _runner(run_dir, adapters)
    runner.run()

    _FORBIDDEN_KEY = "operational_diagnostic"
    for ar_path in run_dir.rglob("analysis_record.json"):
        ar = json.loads(ar_path.read_bytes())
        assert _FORBIDDEN_KEY not in ar, (
            f"analysis_record at {ar_path} has top-level key '{_FORBIDDEN_KEY}'"
        )
        artifact = ar.get("reviewer_artifact")
        if artifact is not None:
            assert _FORBIDDEN_KEY not in artifact, (
                f"reviewer_artifact in {ar_path} has key '{_FORBIDDEN_KEY}'"
            )
            # Also check nested keys one level deep (provenance dict etc.)
            for v in artifact.values():
                if isinstance(v, dict):
                    assert _FORBIDDEN_KEY not in v


def test_worker_writes_diagnostic_stderr_tokens_on_exception(monkeypatch) -> None:
    """When the worker catches an exception, it must emit structured
    AKSHARAMD_SMOKE_* tokens to stderr so the harness can surface them."""
    import benchmarks.parsed_vs_raw.arms.parser_arm as arm

    def _raises(pdf_bytes: bytes):
        raise TypeError("cannot unpack non-sequence tuple of wrong size")

    monkeypatch.setattr(arm, "_compile_pdf_bytes", _raises)
    stdin_bytes, stdout, stderr = _capture_stdio(monkeypatch)
    stdin_bytes.write(b"%PDF-1.5\n")
    stdin_bytes.seek(0)

    from benchmarks.eval_v1.smoke_b1a_7b.workers.main import main
    exit_code = main(["--parser-id", "aksharamd-reference", "--canonical-id", "X"])
    assert exit_code == 3

    err_text = stderr.getvalue()
    assert "AKSHARAMD_SMOKE_DEFECT_REASON:" in err_text
    assert "AKSHARAMD_SMOKE_EXCEPTION_CLASS: TypeError" in err_text
    assert "AKSHARAMD_SMOKE_EXCEPTION_MESSAGE:" in err_text
    assert "AKSHARAMD_SMOKE_TRACEBACK_BEGIN" in err_text
    assert "AKSHARAMD_SMOKE_TRACEBACK_END" in err_text
    # The traceback must contain the exception type.
    assert "TypeError" in err_text
