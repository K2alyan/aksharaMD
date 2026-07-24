"""Smoke tests for the layout-complexity evidence CLI.

Runs the CLI with an empty corpus / fresh tempdir cache so it never
contacts the network and never requires a real PDF. Confirms the
report files are written and the exit code is 0.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.ocr_auto_calibration.run_layout_complexity_evidence import (
    build_arg_parser,
    main,
)


def test_arg_parser_has_expected_flags() -> None:
    parser = build_arg_parser()
    ns = parser.parse_args(
        [
            "--hydrate",
            "--out",
            "some/where",
            "--cache-root",
            "/tmp/cache",
            "--timeout",
            "15",
        ]
    )
    assert ns.hydrate is True
    assert Path(ns.out) == Path("some/where")
    assert Path(ns.cache_root) == Path("/tmp/cache")
    assert ns.timeout == 15.0


def test_cli_dry_run_writes_empty_report(tmp_path: Path) -> None:
    """No hydration, fresh empty cache — the harness must exit 0 and
    write a report noting the absence of captures."""
    out = tmp_path / "results"
    cache = tmp_path / "cache"
    rc = main(["--out", str(out), "--cache-root", str(cache)])
    assert rc == 0
    assert (out / "REPORT.md").exists()
    assert (out / "capture.json").exists()
    assert (out / "analysis.json").exists()
    body = (out / "REPORT.md").read_text(encoding="utf-8")
    assert "No documents captured" in body
